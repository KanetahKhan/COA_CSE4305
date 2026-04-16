#!/usr/bin/env python3
"""
Side-by-Side Policy Comparison Window
======================================
Runs the same request sequence through two independent cache configurations
and presents a comparative stats table, hit-rate bar chart, and per-request
hit/miss breakdown.
"""

import tkinter as tk
from tkinter import ttk, messagebox
import sys, os

sys.path.insert(0, os.path.dirname(__file__))

from cache_controller import RequestType, Policy
from simulator import Simulator

# ── colour palette (matches main GUI) ─────────────────────────────────────────
BG_COLOR   = "#1e1e2e"
PANEL_BG   = "#2a2a3d"
TEXT_COLOR = "#cdd6f4"
ACCENT     = "#89b4fa"
GREEN      = "#a6e3a1"
RED        = "#f38ba8"
YELLOW     = "#f9e2af"
CYAN       = "#89dceb"
DIM        = "#6c7086"
PURPLE     = "#cba6f7"

ASSOC_OPTIONS  = ["1 (Direct)", "2-way", "4-way", "8-way (Fully Assoc.)"]
POLICY_OPTIONS = ["LRU", "LFU", "Random"]

_ASSOC_MAP = {
    "1 (Direct)":           1,
    "2-way":                2,
    "4-way":                4,
    "8-way (Fully Assoc.)": 8,
}
_POLICY_MAP = {
    "LRU":    Policy.LRU,
    "LFU":    Policy.LFU,
    "Random": Policy.RANDOM,
}

# ── built-in presets (two extras that highlight policy differences) ────────────
PRESETS = {
    "Read Miss → Hit": [
        (RequestType.READ,  0x0100, 0),
        (RequestType.READ,  0x0101, 0),
    ],
    "Write-Allocate": [
        (RequestType.WRITE, 0x0202, 0xFF),
        (RequestType.READ,  0x0202, 0),
        (RequestType.READ,  0x0200, 0),
    ],
    "Dirty Eviction": [
        (RequestType.WRITE, 0x0001, 0xEE),
        (RequestType.READ,  0x0100, 0),
    ],
    "Clean Eviction": [
        (RequestType.READ,  0x0000, 0),
        (RequestType.READ,  0x0100, 0),
    ],
    "Write + Readback": [
        (RequestType.WRITE, 0x0000, 0xA1),
        (RequestType.WRITE, 0x0004, 0xB2),
        (RequestType.WRITE, 0x0008, 0xC3),
        (RequestType.READ,  0x0000, 0),
        (RequestType.READ,  0x0004, 0),
        (RequestType.READ,  0x0008, 0),
    ],
    "Stress Test": [
        (RequestType.WRITE, 0x0000, 0xF0),
        (RequestType.WRITE, 0x0100, 0xF1),
        (RequestType.READ,  0x0200, 0),
        (RequestType.READ,  0x0000, 0),
        (RequestType.WRITE, 0x0000, 0xAA),
        (RequestType.READ,  0x0100, 0),
    ],
    # ── presets that make Direct vs 2-way differences obvious ──────────────────
    "Direct vs 2-way (thrash)": [
        # 0x0000 and 0x0100 alias to same index in direct-mapped.
        # In 2-way they both fit; in direct they thrash each other.
        (RequestType.READ,  0x0000, 0),
        (RequestType.READ,  0x0100, 0),
        (RequestType.READ,  0x0000, 0),   # HIT in 2-way,  MISS in direct
        (RequestType.READ,  0x0100, 0),   # HIT in 2-way,  MISS in direct
        (RequestType.READ,  0x0200, 0),
        (RequestType.READ,  0x0000, 0),   # HIT in 2-way,  MISS in direct
        (RequestType.READ,  0x0100, 0),   # HIT in 2-way,  MISS in direct
        (RequestType.READ,  0x0200, 0),
    ],
    "LRU vs LFU (freq matters)": [
        # A (0x0000) is accessed 3× early → high freq.
        # B (0x0100) is accessed once.
        # C (0x0200) then forces eviction.
        #   LRU evicts whichever of A/B was accessed least recently → A
        #   LFU evicts B (freq=1) and keeps A (freq=3)
        # Then re-accessing A is a hit under LFU, miss under LRU.
        (RequestType.READ,  0x0000, 0),
        (RequestType.READ,  0x0000, 0),
        (RequestType.READ,  0x0000, 0),
        (RequestType.READ,  0x0100, 0),
        (RequestType.READ,  0x0200, 0),   # forces eviction of A or B in 2-way
        (RequestType.READ,  0x0000, 0),   # HIT under LFU, MISS under LRU
        (RequestType.READ,  0x0100, 0),
    ],
}

# Default memory regions pre-loaded into both simulators
_MEM_INIT = [
    (0x0000, [0x11, 0x22, 0x33, 0x44]),
    (0x0100, [0xAA, 0xBB, 0xCC, 0xDD]),
    (0x0200, [0x10, 0x20, 0x30, 0x40]),
    (0x0300, [0xDE, 0xAD, 0xBE, 0xEF]),
]


# ── simulation helper ──────────────────────────────────────────────────────────

def _run_sim(requests, assoc, policy):
    """Run one silent simulation and return enriched stats."""
    sim = Simulator(
        num_cache_lines=8, block_size=4, addr_bits=16,
        mem_read_latency=3, mem_write_latency=2,
        associativity=assoc, policy=policy, verbose=False,
    )
    for addr, vals in _MEM_INIT:
        sim.init_memory(addr, vals)

    result = sim.run(requests)
    log = result["log"]

    write_backs = result["stats"].get("bus_writes", 0)
    allocations = sum(1 for e in log if e["event"].startswith("ALLOCATE_DONE"))

    # One HIT/MISS outcome per logical request, in order
    outcomes = [
        "HIT" if e["event"].startswith("CACHE_HIT") else "MISS"
        for e in log
        if e["event"].startswith("CACHE_HIT") or e["event"].startswith("CACHE_MISS")
    ]

    return {
        "stats":       result["stats"],
        "write_backs": write_backs,
        "allocations": allocations,
        "outcomes":    outcomes,
    }


def _config_label(assoc_str, policy_str):
    assoc = _ASSOC_MAP.get(assoc_str, 1)
    if assoc == 1:
        return "Direct-Mapped"
    return f"{assoc}-way / {policy_str}"


# ── main window class ──────────────────────────────────────────────────────────

class CompareWindow(tk.Toplevel):

    def __init__(self, parent, initial_requests=None):
        super().__init__(parent)
        self.title("Side-by-Side Policy Comparison")
        self.configure(bg=BG_COLOR)
        self.geometry("1150x800")
        self.minsize(960, 700)
        self.resizable(True, True)

        self._requests = list(initial_requests) if initial_requests else []
        self._setup_styles()
        self._build_ui()
        self._update_queue_display()

    # ── ttk style ─────────────────────────────────────────────────────────────

    def _setup_styles(self):
        s = ttk.Style(self)
        s.configure("Cmp.TFrame",         background=BG_COLOR)
        s.configure("CmpPanel.TFrame",    background=PANEL_BG)
        s.configure("CmpTitle.TLabel",    background=PANEL_BG, foreground=ACCENT,
                    font=("Consolas", 11, "bold"))
        s.configure("CmpDim.TLabel",      background=PANEL_BG, foreground=DIM,
                    font=("Consolas", 9))
        s.configure("Cmp.Treeview",
                    background="#1a1a2e", foreground=TEXT_COLOR,
                    fieldbackground="#1a1a2e",
                    font=("Consolas", 10), rowheight=24)
        s.configure("Cmp.Treeview.Heading",
                    background="#313244", foreground=ACCENT,
                    font=("Consolas", 10, "bold"))
        s.map("Cmp.Treeview", background=[("selected", "#45475a")])

    # ── layout ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = ttk.Frame(self, style="Cmp.TFrame")
        root.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # ── top: config A | config B | requests ──────────────────────────────
        top = ttk.Frame(root, style="Cmp.TFrame")
        top.pack(fill=tk.X, pady=(0, 8))

        self._build_config_panel(top, "A")
        self._build_config_panel(top, "B")
        self._build_request_panel(top)

        # ── run button row ────────────────────────────────────────────────────
        btn_row = ttk.Frame(root, style="Cmp.TFrame")
        btn_row.pack(fill=tk.X, pady=(0, 8))

        self.run_btn = tk.Button(
            btn_row, text="▶  Run Comparison",
            command=self._run_comparison,
            bg="#313244", fg=GREEN,
            font=("Consolas", 12, "bold"),
            relief=tk.FLAT, padx=24, pady=6,
            activebackground="#45475a", activeforeground=GREEN,
        )
        self.run_btn.pack(side=tk.LEFT, padx=(0, 16))

        self.status_lbl = tk.Label(
            btn_row,
            text="Configure both policies, add requests, then click Run.",
            bg=BG_COLOR, fg=DIM, font=("Consolas", 9),
        )
        self.status_lbl.pack(side=tk.LEFT)

        # ── bottom: stats + chart (left)  |  per-request breakdown (right) ───
        bottom = ttk.Frame(root, style="Cmp.TFrame")
        bottom.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(bottom, style="Cmp.TFrame")
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 6))
        self._build_stats_table(left)
        self._build_chart(left)

        right = ttk.Frame(bottom, style="Cmp.TFrame")
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(6, 0))
        self._build_breakdown_table(right)

    # ── config panel (A or B) ─────────────────────────────────────────────────

    def _build_config_panel(self, parent, label):
        accent = CYAN if label == "A" else YELLOW
        frame = ttk.Frame(parent, style="CmpPanel.TFrame")
        frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 6))

        tk.Label(frame, text=f"  Config {label}", bg=PANEL_BG, fg=accent,
                 font=("Consolas", 13, "bold")).pack(anchor=tk.W, padx=10, pady=(10, 8))

        tk.Label(frame, text="Associativity:", bg=PANEL_BG, fg=DIM,
                 font=("Consolas", 9)).pack(anchor=tk.W, padx=12)

        default_assoc = "1 (Direct)" if label == "A" else "2-way"
        assoc_var = tk.StringVar(value=default_assoc)
        assoc_cb  = ttk.Combobox(frame, textvariable=assoc_var,
                                 values=ASSOC_OPTIONS, state="readonly", width=20)
        assoc_cb.pack(padx=12, pady=(2, 8))

        tk.Label(frame, text="Replacement Policy:", bg=PANEL_BG, fg=DIM,
                 font=("Consolas", 9)).pack(anchor=tk.W, padx=12)

        policy_var = tk.StringVar(value="LRU")
        policy_cb  = ttk.Combobox(frame, textvariable=policy_var,
                                  values=POLICY_OPTIONS,
                                  state="disabled" if label == "A" else "readonly",
                                  width=12)
        policy_cb.pack(padx=12, pady=(2, 12))

        # Disable policy dropdown when direct-mapped is selected
        def _on_assoc(_e=None):
            a = _ASSOC_MAP.get(assoc_var.get(), 1)
            policy_cb.configure(state="disabled" if a == 1 else "readonly")
        assoc_cb.bind("<<ComboboxSelected>>", _on_assoc)

        if label == "A":
            self._assoc_a  = assoc_var
            self._policy_a = policy_var
        else:
            self._assoc_b  = assoc_var
            self._policy_b = policy_var

    # ── request panel ─────────────────────────────────────────────────────────

    def _build_request_panel(self, parent):
        frame = ttk.Frame(parent, style="CmpPanel.TFrame")
        frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        tk.Label(frame, text="  Request Sequence", bg=PANEL_BG, fg=ACCENT,
                 font=("Consolas", 12, "bold")).pack(anchor=tk.W, padx=10, pady=(10, 6))

        # Preset row
        p_row = ttk.Frame(frame, style="CmpPanel.TFrame")
        p_row.pack(fill=tk.X, padx=10, pady=(0, 4))

        tk.Label(p_row, text="Preset:", bg=PANEL_BG, fg=DIM,
                 font=("Consolas", 9)).pack(side=tk.LEFT)
        self._preset_var = tk.StringVar()
        ttk.Combobox(p_row, textvariable=self._preset_var,
                     values=list(PRESETS.keys()),
                     state="readonly", width=26).pack(side=tk.LEFT, padx=4)
        tk.Button(p_row, text="Load", command=self._load_preset,
                  bg="#45475a", fg=TEXT_COLOR, font=("Consolas", 9),
                  relief=tk.FLAT, padx=6).pack(side=tk.LEFT, padx=2)
        tk.Button(p_row, text="Clear", command=self._clear_requests,
                  bg="#45475a", fg=RED, font=("Consolas", 9),
                  relief=tk.FLAT, padx=6).pack(side=tk.LEFT, padx=2)

        # Manual add row
        a_row = ttk.Frame(frame, style="CmpPanel.TFrame")
        a_row.pack(fill=tk.X, padx=10, pady=(0, 4))

        self._rtype_var = tk.StringVar(value="READ")
        tk.Radiobutton(a_row, text="READ",  variable=self._rtype_var, value="READ",
                       bg=PANEL_BG, fg=GREEN, selectcolor=PANEL_BG,
                       font=("Consolas", 9), activebackground=PANEL_BG).pack(side=tk.LEFT)
        tk.Radiobutton(a_row, text="WRITE", variable=self._rtype_var, value="WRITE",
                       bg=PANEL_BG, fg=RED,  selectcolor=PANEL_BG,
                       font=("Consolas", 9), activebackground=PANEL_BG).pack(side=tk.LEFT, padx=(6, 0))
        tk.Label(a_row, text="  Addr 0x", bg=PANEL_BG, fg=DIM,
                 font=("Consolas", 9)).pack(side=tk.LEFT)
        self._addr_entry = tk.Entry(a_row, width=5, bg="#45475a", fg=TEXT_COLOR,
                                    font=("Consolas", 9), insertbackground=TEXT_COLOR)
        self._addr_entry.insert(0, "0000")
        self._addr_entry.pack(side=tk.LEFT, padx=2)
        tk.Label(a_row, text="Data 0x", bg=PANEL_BG, fg=DIM,
                 font=("Consolas", 9)).pack(side=tk.LEFT, padx=(6, 0))
        self._data_entry = tk.Entry(a_row, width=3, bg="#45475a", fg=TEXT_COLOR,
                                    font=("Consolas", 9), insertbackground=TEXT_COLOR)
        self._data_entry.insert(0, "00")
        self._data_entry.pack(side=tk.LEFT, padx=2)
        tk.Button(a_row, text="+ Add", command=self._add_request,
                  bg="#45475a", fg=GREEN, font=("Consolas", 9, "bold"),
                  relief=tk.FLAT, padx=6).pack(side=tk.LEFT, padx=(6, 0))

        # Scrollable queue display
        self._queue_text = tk.Text(
            frame, bg="#1a1a2e", fg=TEXT_COLOR,
            font=("Consolas", 9), height=6,
            relief=tk.FLAT, state=tk.DISABLED,
        )
        self._queue_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        self._queue_text.tag_configure("r", foreground=GREEN)
        self._queue_text.tag_configure("w", foreground=RED)
        self._queue_text.tag_configure("d", foreground=DIM)

    # ── stats table ───────────────────────────────────────────────────────────

    def _build_stats_table(self, parent):
        frame = ttk.Frame(parent, style="CmpPanel.TFrame")
        frame.pack(fill=tk.X, pady=(0, 6))

        tk.Label(frame, text="  Comparison Results", bg=PANEL_BG, fg=ACCENT,
                 font=("Consolas", 11, "bold")).pack(anchor=tk.W, padx=10, pady=(10, 4))

        cols = ("Metric", "Config A", "Config B", "Winner")
        self._stats_tree = ttk.Treeview(frame, columns=cols,
                                        show="headings", height=6,
                                        style="Cmp.Treeview")
        for col, w in zip(cols, [160, 160, 160, 80]):
            self._stats_tree.heading(col, text=col)
            self._stats_tree.column(col, width=w, anchor=tk.CENTER)

        self._stats_tree.tag_configure("win_a", foreground=CYAN)
        self._stats_tree.tag_configure("win_b", foreground=YELLOW)
        self._stats_tree.tag_configure("tie",   foreground=DIM)

        self._stats_tree.pack(fill=tk.X, padx=10, pady=(0, 10))

    # ── hit-rate bar chart ────────────────────────────────────────────────────

    def _build_chart(self, parent):
        frame = ttk.Frame(parent, style="CmpPanel.TFrame")
        frame.pack(fill=tk.X)

        tk.Label(frame, text="  Hit Rate", bg=PANEL_BG, fg=ACCENT,
                 font=("Consolas", 11, "bold")).pack(anchor=tk.W, padx=10, pady=(10, 4))

        self._chart = tk.Canvas(frame, bg="#1a1a2e", height=120,
                                highlightthickness=0)
        self._chart.pack(fill=tk.X, padx=10, pady=(0, 10))
        self._draw_chart_empty()

    # ── per-request breakdown ─────────────────────────────────────────────────

    def _build_breakdown_table(self, parent):
        frame = ttk.Frame(parent, style="CmpPanel.TFrame")
        frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(frame, text="  Per-Request Breakdown", bg=PANEL_BG, fg=ACCENT,
                 font=("Consolas", 11, "bold")).pack(anchor=tk.W, padx=10, pady=(10, 4))

        cols = ("#", "Type", "Address", "Data", "Config A", "Config B")
        self._bdown_tree = ttk.Treeview(frame, columns=cols,
                                        show="headings",
                                        style="Cmp.Treeview")
        for col, w in zip(cols, [32, 52, 72, 52, 110, 110]):
            self._bdown_tree.heading(col, text=col)
            self._bdown_tree.column(col, width=w, anchor=tk.CENTER)

        # Colour key: both hit = green, only A = cyan, only B = yellow, both miss = red
        self._bdown_tree.tag_configure("hh", foreground=GREEN)
        self._bdown_tree.tag_configure("hm", foreground=CYAN)
        self._bdown_tree.tag_configure("mh", foreground=YELLOW)
        self._bdown_tree.tag_configure("mm", foreground=RED)

        sb = ttk.Scrollbar(frame, orient=tk.VERTICAL,
                           command=self._bdown_tree.yview)
        self._bdown_tree.configure(yscrollcommand=sb.set)
        self._bdown_tree.pack(side=tk.LEFT, fill=tk.BOTH,
                              expand=True, padx=(10, 0), pady=(0, 10))
        sb.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 10), pady=(0, 10))

        # Legend
        leg = ttk.Frame(parent, style="CmpPanel.TFrame")
        leg.pack(fill=tk.X, padx=10, pady=(0, 8))
        for colour, text in [(GREEN, "Both HIT"), (CYAN, "Only A hit"),
                             (YELLOW, "Only B hit"), (RED, "Both MISS")]:
            tk.Label(leg, text=f"■ {text}", bg=PANEL_BG, fg=colour,
                     font=("Consolas", 8)).pack(side=tk.LEFT, padx=6)

    # ── chart drawing ─────────────────────────────────────────────────────────

    def _draw_chart_empty(self):
        c = self._chart
        c.delete("all")
        w = c.winfo_width() or 500
        c.create_text(w // 2, 55, text="Run a comparison to see the chart",
                      fill=DIM, font=("Consolas", 9))

    def _draw_chart(self, rate_a, rate_b, label_a, label_b):
        c = self._chart
        c.delete("all")
        c.update_idletasks()
        W = c.winfo_width() or 560
        H = 120

        pad_l  = 10
        pad_r  = 10
        bar_h  = 30
        gap    = 14
        label_w = 0          # labels go on the right side after the bar track
        track_w = W - pad_l - pad_r - 90   # 90 px reserved for right-side label

        configs = [
            (rate_a, label_a, CYAN,   "#1a4060", pad_l, 12),
            (rate_b, label_b, YELLOW, "#4a3a00", pad_l, 12 + bar_h + gap),
        ]

        # guide lines at 25 / 50 / 75 / 100 %
        for pct in (25, 50, 75, 100):
            x = pad_l + int(track_w * pct / 100)
            c.create_line(x, 6, x, H - 18, fill="#313244", dash=(2, 4))
            c.create_text(x, H - 6, text=f"{pct}%",
                          fill=DIM, font=("Consolas", 7), anchor=tk.S)

        for rate, label, fg, track_fill, x0, y0 in configs:
            y1    = y0 + bar_h
            bar_w = int(track_w * min(rate, 100) / 100)

            # track
            c.create_rectangle(x0, y0, x0 + track_w, y1,
                               fill="#252535", outline="")
            # filled bar
            if bar_w > 0:
                c.create_rectangle(x0, y0, x0 + bar_w, y1,
                                   fill=fg, outline="")
                # percentage text inside bar (if there's room)
                if bar_w > 42:
                    c.create_text(x0 + bar_w - 4, (y0 + y1) // 2,
                                  text=f"{rate:.1f}%",
                                  fill="#1e1e2e", font=("Consolas", 9, "bold"),
                                  anchor=tk.E)

            # right-side label
            c.create_text(x0 + track_w + 6, (y0 + y1) // 2,
                          text=f"{label}",
                          fill=fg, font=("Consolas", 8, "bold"), anchor=tk.W)

    # ── queue helpers ─────────────────────────────────────────────────────────

    def _update_queue_display(self):
        t = self._queue_text
        t.configure(state=tk.NORMAL)
        t.delete("1.0", tk.END)
        if not self._requests:
            t.insert(tk.END, "(empty — load a preset or add requests above)", "d")
        else:
            for i, (rt, addr, data) in enumerate(self._requests):
                kind = "READ " if rt == RequestType.READ else "WRITE"
                tag  = "r"    if rt == RequestType.READ else "w"
                line = f"{i+1:>2}. {kind}  0x{addr:04X}"
                if rt == RequestType.WRITE:
                    line += f"  ← 0x{data:02X}"
                t.insert(tk.END, line + "\n", tag)
        t.configure(state=tk.DISABLED)

    def _add_request(self):
        try:
            addr = int(self._addr_entry.get(), 16)
        except ValueError:
            messagebox.showerror("Invalid Address",
                                 "Enter a valid hex address (e.g. 0100)", parent=self)
            return
        try:
            data = int(self._data_entry.get(), 16)
        except ValueError:
            messagebox.showerror("Invalid Data",
                                 "Enter a valid hex value (e.g. FF)", parent=self)
            return
        rt = (RequestType.READ if self._rtype_var.get() == "READ"
              else RequestType.WRITE)
        self._requests.append((rt, addr, data))
        self._update_queue_display()

    def _load_preset(self):
        name = self._preset_var.get()
        if name and name in PRESETS:
            self._requests = list(PRESETS[name])
            self._update_queue_display()

    def _clear_requests(self):
        self._requests = []
        self._update_queue_display()

    # ── comparison runner ─────────────────────────────────────────────────────

    def _resolve_config(self, which):
        assoc_str  = (self._assoc_a  if which == "A" else self._assoc_b).get()
        policy_str = (self._policy_a if which == "A" else self._policy_b).get()
        assoc  = _ASSOC_MAP.get(assoc_str, 1)
        policy = Policy.DIRECT if assoc == 1 else _POLICY_MAP.get(policy_str, Policy.LRU)
        return assoc, policy, _config_label(assoc_str, policy_str)

    def _run_comparison(self):
        if not self._requests:
            messagebox.showwarning("No Requests",
                                   "Add at least one request before running.",
                                   parent=self)
            return

        assoc_a, policy_a, label_a = self._resolve_config("A")
        assoc_b, policy_b, label_b = self._resolve_config("B")

        self.status_lbl.configure(text="Running…", fg=YELLOW)
        self.update_idletasks()

        try:
            res_a = _run_sim(self._requests, assoc_a, policy_a)
            res_b = _run_sim(self._requests, assoc_b, policy_b)
        except Exception as exc:
            messagebox.showerror("Simulation Error", str(exc), parent=self)
            self.status_lbl.configure(text=f"Error: {exc}", fg=RED)
            return

        self._fill_stats(res_a, res_b, label_a, label_b)
        self._fill_breakdown(res_a, res_b, label_a, label_b)

        rate_a = res_a["stats"]["hits"] / max(res_a["stats"]["total_requests"], 1) * 100
        rate_b = res_b["stats"]["hits"] / max(res_b["stats"]["total_requests"], 1) * 100
        self._draw_chart(rate_a, rate_b, label_a, label_b)

        winner = label_a if rate_a > rate_b else (label_b if rate_b > rate_a else "Tie")
        self.status_lbl.configure(
            text=(f"Done — {len(self._requests)} requests  |  "
                  f"{label_a}: {rate_a:.1f}%  vs  {label_b}: {rate_b:.1f}%"
                  f"  |  Winner: {winner}"),
            fg=GREEN,
        )

    # ── populate stats table ──────────────────────────────────────────────────

    def _fill_stats(self, res_a, res_b, label_a, label_b):
        for item in self._stats_tree.get_children():
            self._stats_tree.delete(item)

        self._stats_tree.heading("Config A", text=f"A: {label_a}")
        self._stats_tree.heading("Config B", text=f"B: {label_b}")

        sa, sb = res_a["stats"], res_b["stats"]

        def _w(va, vb, higher_better=True):
            """Return (tag, symbol) for the winner cell."""
            if va == vb:
                return "tie", "—"
            better_a = (va > vb) if higher_better else (va < vb)
            return ("win_a", "A  ✓") if better_a else ("win_b", "B  ✓")

        hit_a = sa["hits"] / max(sa["total_requests"], 1) * 100
        hit_b = sb["hits"] / max(sb["total_requests"], 1) * 100

        amat_a = sa.get("amat", 0)
        amat_b = sb.get("amat", 0)
        cpi_a  = sa.get("effective_cpi", 0)
        cpi_b  = sb.get("effective_cpi", 0)
        ipc_a  = sa.get("achieved_ipc", 0)
        ipc_b  = sb.get("achieved_ipc", 0)

        rows = [
            ("Hit Rate",      f"{hit_a:.1f}%",               f"{hit_b:.1f}%",
             _w(hit_a, hit_b, True)),
            ("Hits",          str(sa["hits"]),                str(sb["hits"]),
             _w(sa["hits"], sb["hits"], True)),
            ("Misses",        str(sa["misses"]),              str(sb["misses"]),
             _w(sa["misses"], sb["misses"], False)),
            ("  Compulsory",  str(sa.get("compulsory_misses", 0)),
                              str(sb.get("compulsory_misses", 0)),
             _w(sa.get("compulsory_misses", 0), sb.get("compulsory_misses", 0), False)),
            ("  Conflict",    str(sa.get("conflict_misses", 0)),
                              str(sb.get("conflict_misses", 0)),
             _w(sa.get("conflict_misses", 0), sb.get("conflict_misses", 0), False)),
            ("AMAT (cycles)", str(amat_a),                    str(amat_b),
             _w(amat_a, amat_b, False)),
            ("Eff CPI",       str(cpi_a),                     str(cpi_b),
             _w(cpi_a, cpi_b, False)),
            ("Throughput IPC",str(ipc_a),                     str(ipc_b),
             _w(ipc_a, ipc_b, True)),
            ("Total Cycles",  str(sa["total_cycles"]),        str(sb["total_cycles"]),
             _w(sa["total_cycles"], sb["total_cycles"], False)),
            ("Write-Backs",   str(res_a["write_backs"]),      str(res_b["write_backs"]),
             _w(res_a["write_backs"], res_b["write_backs"], False)),
            ("Allocations",   str(res_a["allocations"]),      str(res_b["allocations"]),
             _w(res_a["allocations"], res_b["allocations"], False)),
        ]

        if sa.get("l2_enabled") or sb.get("l2_enabled"):
            rows.extend([
                ("L2 Hits",        str(sa.get("l2_hits", 0)), str(sb.get("l2_hits", 0)),
                 _w(sa.get("l2_hits", 0), sb.get("l2_hits", 0), True)),
                ("L2 Misses",      str(sa.get("l2_misses", 0)), str(sb.get("l2_misses", 0)),
                 _w(sa.get("l2_misses", 0), sb.get("l2_misses", 0), False)),
                ("L2 Local Miss",  str(sa.get("l2_local_miss_rate", "N/A")),
                                   str(sb.get("l2_local_miss_rate", "N/A")),
                 _w(sa.get("l2_local_miss_rate_value", 0),
                    sb.get("l2_local_miss_rate_value", 0), False)),
                ("L2 Global Miss", str(sa.get("l2_global_miss_rate", "N/A")),
                                   str(sb.get("l2_global_miss_rate", "N/A")),
                 _w(sa.get("l2_global_miss_rate_value", 0),
                    sb.get("l2_global_miss_rate_value", 0), False)),
            ])

        for metric, va, vb, (tag, sym) in rows:
            self._stats_tree.insert("", tk.END,
                                    values=(metric, va, vb, sym),
                                    tags=(tag,))

    # ── populate breakdown table ──────────────────────────────────────────────

    def _fill_breakdown(self, res_a, res_b, label_a, label_b):
        for item in self._bdown_tree.get_children():
            self._bdown_tree.delete(item)

        self._bdown_tree.heading("Config A", text=f"A: {label_a}")
        self._bdown_tree.heading("Config B", text=f"B: {label_b}")

        oa = res_a["outcomes"]
        ob = res_b["outcomes"]

        for i, (rt, addr, data) in enumerate(self._requests):
            outcome_a = oa[i] if i < len(oa) else "—"
            outcome_b = ob[i] if i < len(ob) else "—"

            kind  = "READ"  if rt == RequestType.READ  else "WRITE"
            d_str = "—"     if rt == RequestType.READ  else f"0x{data:02X}"
            a_str = ("✓ HIT" if outcome_a == "HIT" else "✗ MISS")
            b_str = ("✓ HIT" if outcome_b == "HIT" else "✗ MISS")

            tag = ("hh" if outcome_a == "HIT"  and outcome_b == "HIT"  else
                   "hm" if outcome_a == "HIT"  and outcome_b == "MISS" else
                   "mh" if outcome_a == "MISS" and outcome_b == "HIT"  else
                   "mm")

            self._bdown_tree.insert("", tk.END,
                                    values=(i + 1, kind, f"0x{addr:04X}",
                                            d_str, a_str, b_str),
                                    tags=(tag,))


# ── standalone entry point ────────────────────────────────────────────────────

def main():
    root = tk.Tk()
    root.withdraw()
    win = CompareWindow(root)
    win.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()


if __name__ == "__main__":
    main()
