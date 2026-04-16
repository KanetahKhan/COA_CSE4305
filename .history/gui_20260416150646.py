#!/usr/bin/env python3
"""
Cache Controller FSM Simulator — GUI Version
=============================================
Tkinter-based visual simulator showing:
  - FSM state diagram with live transitions
  - Cache table with set/way/valid/dirty/tag/data
  - CPU and Memory interface signals
  - Cycle-by-cycle stepping or auto-run
  - Request queue management
  - Event log
  - Configurable associativity (1/2/4/8-way) and replacement policy (LRU/LFU/Random)
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

import math
import copy
import csv
import io
import datetime
from cache_controller import CacheController, State, RequestType, Policy
from memory import Memory
from cpu import CPU
from compare_window import CompareWindow
from memory_window import MemoryWindow
from dataflow_window import DataFlowWindow


STATE_COLORS = {
    State.IDLE:        "#a6e3a1",
    State.COMPARE_TAG: "#89b4fa",
    State.WRITE_BACK:  "#f38ba8",
    State.ALLOCATE:    "#fab387",
}

BG_COLOR   = "#11111b"
PANEL_BG   = "#1e1e2e"
SURFACE    = "#313244"
SURFACE1   = "#45475a"
TEXT_COLOR = "#cdd6f4"
SUBTEXT    = "#a6adc8"
ACCENT     = "#89b4fa"
GREEN      = "#a6e3a1"
RED        = "#f38ba8"
YELLOW     = "#f9e2af"
CYAN       = "#89dceb"
DIM        = "#585b70"
PURPLE     = "#cba6f7"
TEAL       = "#94e2d5"
PEACH      = "#fab387"
PINK       = "#f5c2e7"

ASSOC_OPTIONS  = ["1 (Direct)", "2-way", "4-way", "8-way (Fully Assoc.)"]
POLICY_OPTIONS = ["LRU", "LFU", "Random"]

_ASSOC_MAP = {
    "1 (Direct)":          1,
    "2-way":               2,
    "4-way":               4,
    "8-way (Fully Assoc.)": 8,
}
_POLICY_MAP = {
    "LRU":    Policy.LRU,
    "LFU":    Policy.LFU,
    "Random": Policy.RANDOM,
}


class SimulatorGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Cache Controller FSM Simulator")
        self.root.configure(bg=BG_COLOR)
        self.root.geometry("1340x920")
        self.root.minsize(1100, 800)

        # Configurable cache/memory parameters (edited via Settings dialog)
        self._cfg_num_lines  = 8
        self._cfg_block_size = 4
        self._cfg_addr_bits  = 16
        self._cfg_read_lat   = 3
        self._cfg_write_lat  = 2
        self._cfg_base_cpi   = 1.0

        # Simulation state
        self.cycle           = 0
        self.running         = False
        self.speed           = 500
        self.request_list    = []
        self._mem_op_started = False

        # Step-back / replay history
        self._history   = []      # list of snapshots, one per cycle
        self._log_items = []      # (line_text, tag) pairs mirroring the log widget
        self._hist_pos  = -1      # current position in _history
        self._scrubbing = False   # suppress re-entry in _on_scrub

        # Data flow animation window state
        self._dataflow_window  = None   # DataFlowWindow toplevel (or None if closed)

        # Memory map window state
        self._mem_window       = None   # MemoryWindow toplevel (or None if closed)
        self._mem_active_block = None   # CYAN — block CPU is currently accessing
        self._mem_alloc_block  = None   # GREEN — last block fetched from memory
        self._mem_wb_block     = None   # ORANGE — last block written back

        # Hit/Miss timeline state
        self._timeline_entries = []     # completed requests: {req_type,addr,hit,start,end}
        self._pending_req      = None   # in-flight: {req_type,addr,start} or None

        # Address decomposition tooltip
        self._tooltip_win  = None   # floating Toplevel or None
        self._tooltip_addr = None   # address currently shown (avoid redundant redraws)

        # Preset scenarios
        self.preset_requests = {
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
            "Spatial Locality": [
                # Sequential access within a block — first miss, then all hits
                (RequestType.READ,  0x0000, 0),
                (RequestType.READ,  0x0001, 0),
                (RequestType.READ,  0x0002, 0),
                (RequestType.READ,  0x0003, 0),
                # Next block — one miss, then hits
                (RequestType.READ,  0x0004, 0),
                (RequestType.READ,  0x0005, 0),
                (RequestType.READ,  0x0006, 0),
                (RequestType.READ,  0x0007, 0),
            ],
            "Temporal Locality": [
                # Same address accessed repeatedly — first miss, then all hits
                (RequestType.READ,  0x0100, 0),
                (RequestType.READ,  0x0100, 0),
                (RequestType.READ,  0x0100, 0),
                (RequestType.WRITE, 0x0100, 0xBB),
                (RequestType.READ,  0x0100, 0),
                (RequestType.READ,  0x0100, 0),
            ],
            "Thrashing (Conflict)": [
                # Two addresses map to the same set — every access is a miss
                (RequestType.READ,  0x0000, 0),
                (RequestType.READ,  0x0100, 0),
                (RequestType.READ,  0x0000, 0),
                (RequestType.READ,  0x0100, 0),
                (RequestType.READ,  0x0000, 0),
                (RequestType.READ,  0x0100, 0),
            ],
            "Compulsory Only": [
                # Each address is unique — every miss is compulsory
                (RequestType.READ,  0x0000, 0),
                (RequestType.READ,  0x0100, 0),
                (RequestType.READ,  0x0200, 0),
                (RequestType.READ,  0x0300, 0),
                (RequestType.READ,  0x0400, 0),
                (RequestType.READ,  0x0500, 0),
            ],
        }

        self._build_ui()
        self._create_sim_objects()
        self._init_memory_data()
        self._refresh_all()
        # Snapshot cycle-0 state so Back works from the very first step
        self._history.append(self._take_snapshot())
        self._hist_pos = 0
        self._update_history_scrubber()

    # ------------------------------------------------------------------
    # Simulation object creation (called on every reset)
    # ------------------------------------------------------------------

    def _current_associativity(self):
        return _ASSOC_MAP.get(self.assoc_var.get(), 1)

    def _current_policy(self):
        assoc = self._current_associativity()
        if assoc == 1:
            return Policy.DIRECT
        return _POLICY_MAP.get(self.policy_var.get(), Policy.LRU)

    def _create_sim_objects(self):
        assoc  = self._current_associativity()
        policy = self._current_policy()

        self.cache_ctrl      = CacheController(
            num_lines=self._cfg_num_lines,
            block_size=self._cfg_block_size,
            addr_bits=self._cfg_addr_bits,
            associativity=assoc, policy=policy,
            base_cpi=self._cfg_base_cpi,
        )
        self.memory          = Memory(
            size=2 ** self._cfg_addr_bits,
            block_size=self._cfg_block_size,
            read_latency=self._cfg_read_lat,
            write_latency=self._cfg_write_lat,
        )
        self.cpu             = CPU()
        self._mem_op_started = False
        self.cycle           = 0

    def _init_memory_data(self):
        bs = self._cfg_block_size
        # Generate recognisable patterns that scale with block size
        def _pat(base, step):
            return [((base + i * step) & 0xFF) for i in range(bs)]
        self.memory.init_region(0x0000, _pat(0x11, 0x11))
        self.memory.init_region(0x0100, _pat(0xAA, 0x11))
        self.memory.init_region(0x0200, _pat(0x10, 0x10))

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Dark.TFrame",   background=BG_COLOR)
        style.configure("Panel.TFrame",  background=PANEL_BG)
        style.configure("Dark.TLabel",   background=BG_COLOR,  foreground=TEXT_COLOR,
                        font=("Consolas", 10))
        style.configure("Title.TLabel",  background=PANEL_BG, foreground=ACCENT,
                        font=("Consolas", 11, "bold"))
        style.configure("Dark.TButton",  font=("Consolas", 10))
        style.configure("Dark.TCombobox",font=("Consolas", 10))
        style.configure("TCombobox",     fieldbackground=SURFACE, background=SURFACE,
                        foreground=TEXT_COLOR, selectbackground=SURFACE1,
                        selectforeground=TEXT_COLOR)
        style.map("TCombobox",
                  fieldbackground=[("readonly", SURFACE)],
                  foreground=[("readonly", TEXT_COLOR)],
                  selectbackground=[("readonly", SURFACE1)])

        main = ttk.Frame(self.root, style="Dark.TFrame")
        main.pack(fill=tk.BOTH, expand=True)

        self._build_header(main)

        body = ttk.Frame(main, style="Dark.TFrame")
        body.pack(fill=tk.BOTH, expand=True, padx=10, pady=(4, 10))

        top_frame = ttk.Frame(body, style="Dark.TFrame")
        top_frame.pack(fill=tk.X, pady=(0, 6))

        left_top = tk.Frame(top_frame, bg=PANEL_BG,
                            highlightbackground=SURFACE, highlightthickness=1)
        left_top.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))
        self._build_fsm_panel(left_top)

        right_top = ttk.Frame(top_frame, style="Dark.TFrame")
        right_top.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(5, 0))
        self._build_signals_panel(right_top)
        self._build_controls_panel(right_top)

        bottom_frame = ttk.Frame(body, style="Dark.TFrame")
        bottom_frame.pack(fill=tk.BOTH, expand=True)

        left_bot = tk.Frame(bottom_frame, bg=PANEL_BG,
                            highlightbackground=SURFACE, highlightthickness=1)
        left_bot.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))
        self._build_cache_panel(left_bot)

        right_bot = ttk.Frame(bottom_frame, style="Dark.TFrame")
        right_bot.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(5, 0))
        self._build_request_panel(right_bot)
        self._build_log_panel(right_bot)

        timeline_outer = tk.Frame(body, bg=PANEL_BG,
                                  highlightbackground=SURFACE, highlightthickness=1)
        timeline_outer.pack(fill=tk.X, pady=(6, 0))
        self._build_timeline_panel(timeline_outer)

    def _build_header(self, parent):
        """Gradient title banner at the top of the window."""
        hdr = tk.Canvas(parent, height=54, bg=BG_COLOR, highlightthickness=0)
        hdr.pack(fill=tk.X)

        def _redraw(event=None):
            hdr.delete("all")
            w = hdr.winfo_width() or 1300
            # Gradient background: BG_COLOR → slightly blue-tinted
            steps = 54
            for i in range(steps):
                t   = i / steps
                r   = int(0x11 + t * (0x16 - 0x11))
                g   = int(0x11 + t * (0x16 - 0x11))
                b   = int(0x1b + t * (0x38 - 0x1b))
                col = f"#{r:02x}{g:02x}{b:02x}"
                hdr.create_line(0, i, w, i, fill=col)
            # Bottom accent line
            hdr.create_line(0, 53, w, 53, fill=ACCENT, width=2)
            # Left glow accent
            for i, alpha in enumerate([0.15, 0.10, 0.06]):
                c = int(0x89 * alpha + 0x11 * (1 - alpha))
                hdr.create_line(0, 0, 0+i, 54,
                                fill=f"#{int(0x89*(alpha)):02x}{int(0xb4*(alpha)):02x}{int(0xfa*(alpha)):02x}",
                                width=3)
            # Title
            hdr.create_text(18, 18, text="⚡  CACHE CONTROLLER FSM SIMULATOR",
                            fill=TEXT_COLOR, font=("Consolas", 13, "bold"),
                            anchor=tk.W)
            hdr.create_text(18, 38, text="Computer Organization & Architecture  ·  CSE 4305",
                            fill=DIM, font=("Consolas", 9),
                            anchor=tk.W)
            # Right: version tag
            hdr.create_text(w - 14, 27,
                            text="v2.0  ·  Write-Back/Write-Allocate  ·  LRU/LFU/Random",
                            fill=DIM, font=("Consolas", 8), anchor=tk.E)

        hdr.bind("<Configure>", lambda _e: _redraw())
        _redraw()

    def _panel_header(self, parent, text, accent=ACCENT, bg=PANEL_BG):
        """Panel section header with a colored left accent bar."""
        row = tk.Frame(parent, bg=bg)
        row.pack(fill=tk.X)
        tk.Frame(row, width=3, bg=accent).pack(side=tk.LEFT, fill=tk.Y)
        tk.Label(row, text=f"  {text}", bg=bg, fg=accent,
                 font=("Consolas", 10, "bold")).pack(side=tk.LEFT, pady=(8, 6))
        return row

    def _build_fsm_panel(self, parent):
        self._panel_header(parent, "FSM State Diagram", ACCENT)
        self.fsm_canvas = tk.Canvas(parent, bg="#0d0d18",
                                    highlightthickness=0, height=370)
        self.fsm_canvas.pack(fill=tk.BOTH, expand=True, padx=0, pady=(0, 0))
        self.fsm_canvas.bind("<Configure>", lambda _e: self._draw_fsm())

    def _build_signals_panel(self, parent):
        frame = tk.Frame(parent, bg=PANEL_BG,
                         highlightbackground=SURFACE, highlightthickness=1)
        frame.pack(fill=tk.X, pady=(0, 5))

        self._panel_header(frame, "Interface Signals", TEAL, PANEL_BG)

        sig_frame = tk.Frame(frame, bg=PANEL_BG)
        sig_frame.pack(fill=tk.X, padx=12, pady=(0, 10))

        self.signal_labels = {}
        self.signal_leds   = {}   # canvas → oval item id
        cpu_signals = ["cpu_valid", "cpu_rd_wr", "cpu_addr", "cpu_data",
                       "cpu_ready", "cpu_stall"]
        mem_signals = ["mem_read", "mem_write", "mem_addr", "mem_ready"]

        def _sig_col(parent, heading, heading_color, signals):
            tk.Label(parent, text=heading, bg=PANEL_BG, fg=heading_color,
                     font=("Consolas", 8, "bold")).pack(anchor=tk.W, pady=(0, 2))
            for sig in signals:
                row = tk.Frame(parent, bg=PANEL_BG)
                row.pack(anchor=tk.W, pady=1)
                # LED dot
                led = tk.Canvas(row, width=9, height=9, bg=PANEL_BG,
                                highlightthickness=0)
                led.pack(side=tk.LEFT, padx=(0, 5))
                oid = led.create_oval(1, 1, 8, 8, fill=DIM, outline="")
                self.signal_leds[sig] = (led, oid)
                lbl = tk.Label(row, text=f"{sig}: —", bg=PANEL_BG, fg=DIM,
                               font=("Consolas", 9), anchor=tk.W)
                lbl.pack(side=tk.LEFT)
                self.signal_labels[sig] = lbl

        col1 = tk.Frame(sig_frame, bg=PANEL_BG)
        col1.pack(side=tk.LEFT, fill=tk.X, expand=True)
        _sig_col(col1, "CPU ↔ Cache", CYAN, cpu_signals)

        col2 = tk.Frame(sig_frame, bg=PANEL_BG)
        col2.pack(side=tk.RIGHT, fill=tk.X, expand=True)
        _sig_col(col2, "Cache ↔ Mem", PEACH, mem_signals)

    def _build_controls_panel(self, parent):
        frame = tk.Frame(parent, bg=PANEL_BG,
                         highlightbackground=SURFACE, highlightthickness=1)
        frame.pack(fill=tk.X, pady=(5, 0))

        self._panel_header(frame, "Controls", PURPLE, PANEL_BG)

        # ── Row 1: Cycle + stats ─────────────────────────────────────
        row1 = tk.Frame(frame, bg=PANEL_BG)
        row1.pack(fill=tk.X, padx=12, pady=(0, 4))

        self.cycle_label = tk.Label(row1, text="Cycle: 0",
                                    bg=PANEL_BG, fg=ACCENT,
                                    font=("Consolas", 16, "bold"))
        self.cycle_label.pack(side=tk.LEFT, padx=(0, 16))

        self.stats_label = tk.Label(row1,
                                    text="Hits: 0  ·  Misses: 0  ·  Rate: —",
                                    bg=PANEL_BG, fg=SUBTEXT,
                                    font=("Consolas", 9))
        self.stats_label.pack(side=tk.LEFT)

        # ── Row 2: Action buttons ────────────────────────────────────
        row2 = tk.Frame(frame, bg=PANEL_BG)
        row2.pack(fill=tk.X, padx=12, pady=(2, 4))

        def btn(parent, text, cmd, fg, bg, abg=None):
            if abg is None:
                abg = bg
            b = tk.Button(parent, text=text, command=cmd,
                          bg=bg, fg=fg, activebackground=abg,
                          activeforeground=TEXT_COLOR,
                          font=("Consolas", 9, "bold"),
                          relief=tk.FLAT, cursor="hand2",
                          padx=10, pady=5,
                          borderwidth=0)
            return b

        self.back_btn = btn(row2, "⏮ Back",  self._step_back,
                            YELLOW, "#2c2514", "#3c3520")
        self.back_btn.configure(state=tk.DISABLED)
        self.back_btn.pack(side=tk.LEFT, padx=(0, 3))

        self.step_btn = btn(row2, "⏭ Step",  self._step,
                            ACCENT, "#162040", "#223060")
        self.step_btn.pack(side=tk.LEFT, padx=(0, 3))

        self.run_btn = btn(row2, "▶ Run",   self._toggle_run,
                           GREEN, "#142414", "#1e3a1e")
        self.run_btn.pack(side=tk.LEFT, padx=(0, 3))

        self.reset_btn = btn(row2, "↺ Reset", self._reset,
                             RED,   "#2e1414", "#421e1e")
        self.reset_btn.pack(side=tk.LEFT, padx=(0, 3))

        btn(row2, "⇌ Compare", self._open_compare,
            PURPLE, "#221432", "#321a48").pack(side=tk.LEFT, padx=(0, 3))

        btn(row2, "🗺 Mem",    self._open_memory_window,
            CYAN,   "#122030", "#1a3040").pack(side=tk.LEFT, padx=(0, 3))

        btn(row2, "⚡ Flow",  self._open_dataflow_window,
            PEACH,  "#2a1a10", "#3c2818").pack(side=tk.LEFT, padx=(0, 3))

        btn(row2, "📄 Export", self._export_report,
            TEAL,   "#122820", "#1a3c2c").pack(side=tk.LEFT, padx=(0, 3))

        btn(row2, "📥 Import", self._import_trace,
            YELLOW, "#2c2514", "#3c3520").pack(side=tk.LEFT, padx=(0, 10))

        tk.Label(row2, text="Speed:", bg=PANEL_BG, fg=DIM,
                 font=("Consolas", 8)).pack(side=tk.LEFT)
        self.speed_scale = tk.Scale(row2, from_=50, to=1000,
                                    orient=tk.HORIZONTAL,
                                    bg=PANEL_BG, fg=SUBTEXT,
                                    troughcolor=SURFACE,
                                    highlightthickness=0,
                                    font=("Consolas", 8), length=110,
                                    command=self._update_speed)
        self.speed_scale.set(500)
        self.speed_scale.pack(side=tk.LEFT, padx=4)

        # ── History scrubber ─────────────────────────────────────────
        row_hist = tk.Frame(frame, bg=PANEL_BG)
        row_hist.pack(fill=tk.X, padx=12, pady=(0, 4))

        tk.Label(row_hist, text="History:", bg=PANEL_BG, fg=DIM,
                 font=("Consolas", 8)).pack(side=tk.LEFT)
        self.hist_scale = tk.Scale(row_hist, from_=0, to=0,
                                   orient=tk.HORIZONTAL,
                                   bg=PANEL_BG, fg=SUBTEXT,
                                   troughcolor=SURFACE,
                                   highlightthickness=0,
                                   font=("Consolas", 8), length=200,
                                   showvalue=False,
                                   command=self._on_scrub)
        self.hist_scale.pack(side=tk.LEFT, padx=4)
        self.hist_pos_lbl = tk.Label(row_hist, text="cycle 0 / 0",
                                     bg=PANEL_BG, fg=DIM,
                                     font=("Consolas", 8))
        self.hist_pos_lbl.pack(side=tk.LEFT, padx=4)

        # ── Row 3: Assoc + Policy + Settings ─────────────────────────
        row3 = tk.Frame(frame, bg=PANEL_BG)
        row3.pack(fill=tk.X, padx=12, pady=(0, 4))

        tk.Label(row3, text="Assoc:", bg=PANEL_BG, fg=DIM,
                 font=("Consolas", 8)).pack(side=tk.LEFT)

        self.assoc_var = tk.StringVar(value="1 (Direct)")
        assoc_combo = ttk.Combobox(row3, textvariable=self.assoc_var,
                                   values=ASSOC_OPTIONS, state="readonly", width=17)
        assoc_combo.pack(side=tk.LEFT, padx=(3, 10))
        assoc_combo.bind("<<ComboboxSelected>>", self._on_assoc_change)

        tk.Label(row3, text="Policy:", bg=PANEL_BG, fg=DIM,
                 font=("Consolas", 8)).pack(side=tk.LEFT)

        self.policy_var = tk.StringVar(value="LRU")
        self.policy_combo = ttk.Combobox(row3, textvariable=self.policy_var,
                                         values=POLICY_OPTIONS, state="disabled",
                                         width=9)
        self.policy_combo.pack(side=tk.LEFT, padx=(3, 0))

        btn(row3, "⚙ Settings", self._open_settings,
            ACCENT, "#1a1e30", "#252940").pack(side=tk.LEFT, padx=(10, 0))

        # ── Row 4: Preset ─────────────────────────────────────────────
        row4 = tk.Frame(frame, bg=PANEL_BG)
        row4.pack(fill=tk.X, padx=12, pady=(0, 6))

        tk.Label(row4, text="Preset:", bg=PANEL_BG, fg=DIM,
                 font=("Consolas", 8)).pack(side=tk.LEFT)
        self.preset_var = tk.StringVar()
        preset_combo = ttk.Combobox(row4, textvariable=self.preset_var,
                                    values=list(self.preset_requests.keys()),
                                    state="readonly", width=20)
        preset_combo.pack(side=tk.LEFT, padx=4)
        preset_combo.bind("<<ComboboxSelected>>", self._load_preset)

        btn(row4, "Load", self._load_preset,
            TEXT_COLOR, SURFACE, SURFACE1).pack(side=tk.LEFT, padx=3)

        self.config_label = tk.Label(frame,
                                     text=self._config_str(),
                                     bg=PANEL_BG, fg=DIM,
                                     font=("Consolas", 8), anchor=tk.W)
        self.config_label.pack(fill=tk.X, padx=12, pady=(0, 6))

    def _config_str(self):
        assoc = self._current_associativity()
        nl    = self._cfg_num_lines
        bs    = self._cfg_block_size
        ab    = self._cfg_addr_bits
        if assoc == 1:
            return (f"Config: Direct-Mapped  |  {nl} lines  |  "
                f"block={bs}  |  {ab}-bit addr  |  Base CPI={self._cfg_base_cpi:.2f}")
        pol  = self.policy_var.get()
        sets = nl // assoc
        return (f"Config: {assoc}-way Set-Assoc  |  {sets} sets × {assoc} ways  |  "
            f"Policy: {pol}  |  block={bs}  |  {ab}-bit addr  |  Base CPI={self._cfg_base_cpi:.2f}")

    def _build_cache_panel(self, parent):
        frame = parent  # parent is already a styled Frame from _build_ui

        self._panel_header(frame, "Cache Contents", GREEN)

        # 7 columns: added "Wr" (write count) for the heatmap
        cols = ("Set", "Way", "V", "D", "Tag", "Wr", "Block Data")
        self.cache_tree = ttk.Treeview(frame, columns=cols,
                                       show="headings", height=8)

        style = ttk.Style()
        style.configure("Treeview",
                        background="#0d0d18", foreground=TEXT_COLOR,
                        fieldbackground="#0d0d18",
                        font=("Consolas", 10), rowheight=26)
        style.configure("Treeview.Heading",
                        background=SURFACE, foreground=ACCENT,
                        font=("Consolas", 9, "bold"), relief="flat")
        style.map("Treeview",
                  background=[("selected", SURFACE1)],
                  foreground=[("selected", TEXT_COLOR)])

        col_widths = [45, 45, 26, 26, 66, 32, 220]
        for col, cw in zip(cols, col_widths):
            self.cache_tree.heading(col, text=col)
            self.cache_tree.column(col, width=cw, anchor=tk.CENTER)

        self.cache_tree.pack(fill=tk.BOTH, expand=True, padx=0, pady=(0, 4))

        # ── heatmap legend ────────────────────────────────────────────
        legend_row = tk.Frame(frame, bg=PANEL_BG)
        legend_row.pack(fill=tk.X, padx=10, pady=(0, 8))

        tk.Label(legend_row, text="Wr heat:", bg=PANEL_BG, fg=DIM,
                 font=("Consolas", 8)).pack(side=tk.LEFT)
        tk.Label(legend_row, text=" cold", bg=PANEL_BG, fg=DIM,
                 font=("Consolas", 8)).pack(side=tk.LEFT, padx=(4, 0))

        self.heat_legend_canvas = tk.Canvas(legend_row, height=12, width=120,
                                            bg=PANEL_BG, highlightthickness=0)
        self.heat_legend_canvas.pack(side=tk.LEFT, padx=4)
        self._draw_heat_legend()

        tk.Label(legend_row, text="hot", bg=PANEL_BG, fg=DIM,
                 font=("Consolas", 8)).pack(side=tk.LEFT)
        self.heat_max_label = tk.Label(legend_row, text="", bg=PANEL_BG, fg=DIM,
                                       font=("Consolas", 8))
        self.heat_max_label.pack(side=tk.LEFT, padx=(6, 0))

    def _build_request_panel(self, parent):
        frame = tk.Frame(parent, bg=PANEL_BG,
                         highlightbackground=SURFACE, highlightthickness=1)
        frame.pack(fill=tk.X, pady=(0, 5))

        self._panel_header(frame, "Add Request", PEACH)

        row = tk.Frame(frame, bg=PANEL_BG)
        row.pack(fill=tk.X, padx=12, pady=(0, 4))

        self.req_type_var = tk.StringVar(value="READ")
        for val, color in [("READ", GREEN), ("WRITE", RED)]:
            tk.Radiobutton(row, text=val, variable=self.req_type_var, value=val,
                           bg=PANEL_BG, fg=color, selectcolor=SURFACE,
                           font=("Consolas", 9), activebackground=PANEL_BG,
                           activeforeground=color,
                           cursor="hand2").pack(side=tk.LEFT, padx=(0, 8))

        row2 = tk.Frame(frame, bg=PANEL_BG)
        row2.pack(fill=tk.X, padx=12, pady=(0, 4))

        tk.Label(row2, text="Addr 0x", bg=PANEL_BG, fg=DIM,
                 font=("Consolas", 9)).pack(side=tk.LEFT)
        self.addr_entry = tk.Entry(row2, width=6, bg=SURFACE, fg=TEXT_COLOR,
                                   font=("Consolas", 10),
                                   insertbackground=ACCENT,
                                   relief=tk.FLAT, highlightthickness=1,
                                   highlightbackground=SURFACE1,
                                   highlightcolor=ACCENT)
        self.addr_entry.insert(0, "0000")
        self.addr_entry.pack(side=tk.LEFT, padx=4)

        tk.Label(row2, text="Data 0x", bg=PANEL_BG, fg=DIM,
                 font=("Consolas", 9)).pack(side=tk.LEFT, padx=(8, 0))
        self.data_entry = tk.Entry(row2, width=4, bg=SURFACE, fg=TEXT_COLOR,
                                   font=("Consolas", 10),
                                   insertbackground=ACCENT,
                                   relief=tk.FLAT, highlightthickness=1,
                                   highlightbackground=SURFACE1,
                                   highlightcolor=ACCENT)
        self.data_entry.insert(0, "00")
        self.data_entry.pack(side=tk.LEFT, padx=4)

        self.add_btn = tk.Button(row2, text="+ Add",
                                 command=self._add_request,
                                 bg="#142414", fg=GREEN,
                                 activebackground="#1e3a1e",
                                 activeforeground=TEXT_COLOR,
                                 font=("Consolas", 9, "bold"),
                                 relief=tk.FLAT, cursor="hand2", padx=8, pady=3)
        self.add_btn.pack(side=tk.LEFT, padx=4)

        self.queue_label = tk.Label(frame, text="Queue: (empty)",
                                    bg=PANEL_BG, fg=DIM,
                                    font=("Consolas", 8), anchor=tk.W,
                                    wraplength=500, justify=tk.LEFT)
        self.queue_label.pack(fill=tk.X, padx=12, pady=(0, 2))

        # Live address decomposition hint
        self.decomp_label = tk.Label(frame, text="",
                                     bg=PANEL_BG, fg=DIM,
                                     font=("Consolas", 8), anchor=tk.W,
                                     wraplength=500, justify=tk.LEFT)
        self.decomp_label.pack(fill=tk.X, padx=12, pady=(0, 8))

        self.addr_entry.bind("<KeyRelease>", lambda _e: self._update_decomp_label())
        self.addr_entry.bind("<FocusIn>",    lambda _e: self._update_decomp_label())

    def _build_log_panel(self, parent):
        frame = tk.Frame(parent, bg=PANEL_BG,
                         highlightbackground=SURFACE, highlightthickness=1)
        frame.pack(fill=tk.BOTH, expand=True)

        self._panel_header(frame, "Event Log", YELLOW)

        self.log_text = scrolledtext.ScrolledText(
            frame, bg="#0a0a14", fg=TEXT_COLOR,
            font=("Consolas", 9), height=10,
            insertbackground=ACCENT, wrap=tk.WORD,
            relief=tk.FLAT, state=tk.DISABLED,
            padx=6, pady=4
        )
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=0, pady=(0, 0))

        self.log_text.bind("<Motion>", self._on_log_motion)
        self.log_text.bind("<Leave>",  self._on_log_leave)

        self.log_text.tag_configure("hit",  foreground=GREEN)
        self.log_text.tag_configure("miss", foreground=RED)
        self.log_text.tag_configure("info", foreground=CYAN)
        self.log_text.tag_configure("warn", foreground=YELLOW)
        self.log_text.tag_configure("done", foreground=GREEN)
        self.log_text.tag_configure("dim",  foreground=DIM)

    # ------------------------------------------------------------------
    # FSM canvas
    # ------------------------------------------------------------------

    def _draw_fsm(self):
        c = self.fsm_canvas
        c.delete("all")

        w = c.winfo_width()  if c.winfo_width()  > 1 else 420
        h = c.winfo_height() if c.winfo_height() > 1 else 370

        # ── dot-grid background ──────────────────────────────────────
        for xi in range(14, w, 22):
            for yi in range(14, h, 22):
                c.create_oval(xi - 1, yi - 1, xi + 1, yi + 1,
                              fill="#1a1a2c", outline="")

        ox = w * 0.5
        oy = h * 0.5

        positions = {
            State.IDLE:        (ox,       oy - 138),
            State.COMPARE_TAG: (ox,       oy - 18),
            State.WRITE_BACK:  (ox - 128, oy + 112),
            State.ALLOCATE:    (ox + 128, oy + 112),
        }

        R       = 46
        current = self.cache_ctrl.state

        arrows = [
            (State.IDLE,        State.COMPARE_TAG, "CPU valid",     10),
            (State.COMPARE_TAG, State.WRITE_BACK,  "Miss\n(dirty)", -22),
            (State.COMPARE_TAG, State.ALLOCATE,    "Miss\n(clean)",  22),
            (State.WRITE_BACK,  State.ALLOCATE,    "Mem\nready",      0),
            (State.ALLOCATE,    State.IDLE,         "Mem\nready",     10),
        ]

        for src, dst, label, offset in arrows:
            x1, y1 = positions[src]
            x2, y2 = positions[dst]
            dx   = x2 - x1;  dy   = y2 - y1
            dist = math.sqrt(dx*dx + dy*dy)
            if dist == 0:
                continue
            ux, uy = dx / dist, dy / dist
            sx = x1 + ux * (R + 4);   sy = y1 + uy * (R + 4)
            ex = x2 - ux * (R + 4);   ey = y2 - uy * (R + 4)

            is_active = (current == dst and self.cache_ctrl.prev_state == src)
            color = YELLOW if is_active else "#3a3a52"
            width = 2.5    if is_active else 1.5

            px = -uy * offset;  py = ux * offset
            mx = (sx + ex) / 2 + px;   my = (sy + ey) / 2 + py

            # Arrow glow for active
            if is_active:
                c.create_line(sx, sy, mx, my, ex, ey, smooth=True,
                              fill="#2a2800", width=8)
            c.create_line(sx, sy, mx, my, ex, ey, smooth=True,
                          fill=color, width=width,
                          arrow=tk.LAST, arrowshape=(11, 14, 5))

            # Label with pill background
            lx = mx + (-uy) * 20;   ly = my + ux * 20
            c.create_rectangle(lx - 20, ly - 8, lx + 20, ly + 8,
                               fill="#0d0d18", outline=color, width=1)
            c.create_text(lx, ly, text=label, fill=color,
                          font=("Consolas", 7), justify=tk.CENTER)

        # ── Hit! self-loop ───────────────────────────────────────────
        hit_x, hit_y = positions[State.COMPARE_TAG]
        is_hit = (current == State.IDLE and
                  self.cache_ctrl.prev_state == State.COMPARE_TAG and
                  self.cycle > 0)
        hit_color = GREEN if is_hit else "#3a3a52"
        hit_w     = 2.5   if is_hit else 1.5

        if is_hit:
            c.create_line(hit_x + R + 4,  hit_y - 10,
                          hit_x + R + 42, hit_y - 32,
                          positions[State.IDLE][0] + R + 4,
                          positions[State.IDLE][1] + 10,
                          smooth=True, fill="#001400", width=8)
        c.create_line(hit_x + R + 4,  hit_y - 10,
                      hit_x + R + 42, hit_y - 32,
                      positions[State.IDLE][0] + R + 4,
                      positions[State.IDLE][1] + 10,
                      smooth=True, fill=hit_color, width=hit_w,
                      arrow=tk.LAST, arrowshape=(11, 14, 5))
        c.create_rectangle(hit_x + R + 18, hit_y - 42,
                           hit_x + R + 60, hit_y - 22,
                           fill="#0d0d18", outline=hit_color, width=1)
        c.create_text(hit_x + R + 39, hit_y - 32, text="Hit!",
                      fill=hit_color, font=("Consolas", 8, "bold"))

        # ── State nodes ──────────────────────────────────────────────
        for state, (x, y) in positions.items():
            is_current = (state == current)
            sc         = STATE_COLORS[state]

            if is_current:
                # Outer glow rings (3 layers, darkening outward)
                for i, (ring_r, alpha_hex) in enumerate(
                        [(R + 18, "28"), (R + 12, "40"), (R + 6, "70")]):
                    # Blend sc with background
                    sr = int(sc[1:3], 16);  sg = int(sc[3:5], 16);  sb = int(sc[5:7], 16)
                    a  = int(alpha_hex, 16) / 255
                    br = int(0x0d);  bg_ = int(0x0d);  bb = int(0x18)
                    mr = int(sr * a + br * (1 - a))
                    mg = int(sg * a + bg_ * (1 - a))
                    mb = int(sb * a + bb * (1 - a))
                    glow_c = f"#{mr:02x}{mg:02x}{mb:02x}"
                    c.create_oval(x - ring_r, y - ring_r,
                                  x + ring_r, y + ring_r,
                                  fill="", outline=glow_c, width=2)

            # Main circle
            fill    = sc if is_current else "#1a1a2c"
            outline = sc
            bw      = 2.5 if is_current else 1.5
            c.create_oval(x - R, y - R, x + R, y + R,
                          fill=fill, outline=outline, width=bw)

            # Inner highlight ring for active state
            if is_current:
                c.create_oval(x - R + 4, y - R + 4,
                              x + R - 4, y + R - 4,
                              fill="", outline="#d9ecff" if is_current else "", width=1)

            text_fg = "#11111b" if is_current else TEXT_COLOR
            c.create_text(x, y, text=state.value, fill=text_fg,
                          font=("Consolas", 9, "bold"))

        # ── Annotation ───────────────────────────────────────────────
        assoc  = self.cache_ctrl.associativity
        policy = self.cache_ctrl.policy
        ann    = "Direct-Mapped" if assoc == 1 else f"{assoc}-way  ·  {policy.value}"
        c.create_rectangle(w - 4, h - 22, w - len(ann)*6 - 12, h - 4,
                           fill="#0d0d18", outline="#1e1e30")
        c.create_text(w - 10, h - 12, text=ann, anchor=tk.SE,
                      fill=DIM, font=("Consolas", 8))

    # ------------------------------------------------------------------
    # Cache table update
    # ------------------------------------------------------------------

    @staticmethod
    def _heat_color(count, max_count):
        """Interpolate a row background from dark-blue (cold) to dark-red (hot)."""
        if max_count == 0 or count == 0:
            return "#1a1a2e"          # same as table bg — effectively invisible
        t = min(count / max_count, 1.0)
        # cold: (22, 30, 74)  →  hot: (80, 20, 22)
        r = int(22  + t * (80  - 22))
        g = int(30  + t * (20  - 30))
        b = int(74  + t * (22  - 74))
        return f"#{r:02x}{g:02x}{b:02x}"

    def _draw_heat_legend(self):
        """Render the cold→hot gradient strip in the legend canvas."""
        c = self.heat_legend_canvas
        c.delete("all")
        w = 120
        steps = 20
        for i in range(steps):
            t   = i / (steps - 1)
            r   = int(22  + t * (80  - 22))
            g   = int(30  + t * (20  - 30))
            b   = int(74  + t * (22  - 74))
            col = f"#{r:02x}{g:02x}{b:02x}"
            x1  = int(i * w / steps)
            x2  = int((i + 1) * w / steps)
            c.create_rectangle(x1, 0, x2, 14, fill=col, outline="")

    def _update_cache_table(self):
        for item in self.cache_tree.get_children():
            self.cache_tree.delete(item)

        ctrl      = self.cache_ctrl
        assoc     = ctrl.associativity
        snapshot  = ctrl.get_cache_snapshot()

        max_wc = max((ln["write_count"] for ln in snapshot), default=0)
        self.heat_max_label.configure(
            text=f"= {max_wc}" if max_wc > 0 else "")

        for line in snapshot:
            v    = "1" if line["valid"] else "0"
            d    = "1" if line["dirty"] else "0"
            tag  = "—" if not line["valid"] else line["tag"]
            data = " ".join(line["data"])
            way  = str(line["way"]) if assoc > 1 else "—"
            wc   = str(line["write_count"]) if line["write_count"] > 0 else "—"

            bg = self._heat_color(line["write_count"], max_wc)

            if line["valid"] and line["dirty"]:
                fg = RED
            elif line["valid"]:
                fg = TEXT_COLOR
            else:
                fg = DIM

            # One composite tag per line slot — encodes both bg and fg
            tag_name = f"heat_{line['set']}_{line['way']}"
            self.cache_tree.tag_configure(tag_name, background=bg, foreground=fg)

            self.cache_tree.insert("", tk.END,
                                   values=(line["set"], way, v, d, tag, wc, data),
                                   tags=(tag_name,))

    # ------------------------------------------------------------------
    # Signals panel update
    # ------------------------------------------------------------------

    def _update_signals(self):
        ctrl = self.cache_ctrl

        def set_sig(name, value, active=False):
            color = GREEN if active else DIM
            self.signal_labels[name].configure(
                text=f"{name}: {value}", fg=color)
            if name in self.signal_leds:
                led, oid = self.signal_leds[name]
                led_color = GREEN if active else "#2a2a3e"
                led.itemconfig(oid, fill=led_color)

        set_sig("cpu_valid", "1" if ctrl.cpu.valid else "0", ctrl.cpu.valid)
        rw = "—"
        if ctrl.cpu.read_write == RequestType.READ:
            rw = "READ"
        elif ctrl.cpu.read_write == RequestType.WRITE:
            rw = "WRITE"
        set_sig("cpu_rd_wr", rw, ctrl.cpu.valid)
        set_sig("cpu_addr",
                f"0x{ctrl.cpu.address:04X}" if ctrl.cpu.valid else "—",
                ctrl.cpu.valid)
        set_sig("cpu_data",
                f"0x{ctrl.cpu.data_out:02X}" if ctrl.cpu.ready else "—",
                ctrl.cpu.ready)
        set_sig("cpu_ready", "1" if ctrl.cpu.ready else "0", ctrl.cpu.ready)
        set_sig("cpu_stall", "1" if ctrl.cpu.stall else "0", ctrl.cpu.stall)

        set_sig("mem_read",  "1" if ctrl.mem.read  else "0", ctrl.mem.read)
        set_sig("mem_write", "1" if ctrl.mem.write else "0", ctrl.mem.write)
        set_sig("mem_addr",
                f"0x{ctrl.mem.address:04X}"
                if (ctrl.mem.read or ctrl.mem.write) else "—",
                ctrl.mem.read or ctrl.mem.write)
        set_sig("mem_ready", "1" if ctrl.mem.ready else "0", ctrl.mem.ready)

    def _update_stats(self):
        stats = self.cache_ctrl.get_stats()
        self.cycle_label.configure(text=f"Cycle: {self.cycle}")
        rate  = stats["hit_rate"] if stats["total_requests"] else "—"
        amat  = f"{stats['amat']:.2f}" if stats["total_requests"] else "—"
        comp  = stats["compulsory_misses"]
        conf  = stats["conflict_misses"]
        self.stats_label.configure(
            text=(f"Hits: {stats['hits']}  ·  Misses: {stats['misses']} "
                  f"(cold:{comp} repl:{conf})  ·  Rate: {rate}  ·  "
                  f"AMAT: {amat}  ·  Stalls: {stats['stall_cycles']}"))

    def _update_queue_display(self):
        if not self.request_list:
            self.queue_label.configure(text="Queue: (empty)")
            return
        parts = []
        for rt, addr, data in self.request_list:
            t = "R" if rt == RequestType.READ else "W"
            if rt == RequestType.WRITE:
                parts.append(f"{t}(0x{addr:04X},0x{data:02X})")
            else:
                parts.append(f"{t}(0x{addr:04X})")
        self.queue_label.configure(text="Queue: " + " → ".join(parts))

    def _refresh_all(self):
        self._draw_fsm()
        self._update_cache_table()
        self._update_signals()
        self._update_stats()
        self._update_queue_display()
        self.config_label.configure(text=self._config_str())
        self._update_memory_window()
        self._update_dataflow_window()
        self._update_timeline()

    # ------------------------------------------------------------------
    # Event log
    # ------------------------------------------------------------------

    def _log(self, message, tag="dim"):
        line = f"[{self.cycle:>4}] {message}\n"
        self._log_items.append((line, tag))
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, line, tag)
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    # ------------------------------------------------------------------
    # Simulation step
    # ------------------------------------------------------------------

    def _step(self):
        # ── replay mode: restore the next already-recorded snapshot ──────────
        if self._hist_pos < len(self._history) - 1:
            self._hist_pos += 1
            self._restore_snapshot(self._history[self._hist_pos])
            self._refresh_after_restore()
            return

        # ── normal forward execution ──────────────────────────────────────────
        if self.cpu.is_done() and self.cache_ctrl.state == State.IDLE:
            if not self.request_list:
                self._log("Simulation complete — no more requests.", "done")
                return

        self.cycle += 1

        new_req = self.cpu.tick(
            self.cache_ctrl.cpu.ready,
            self.cache_ctrl.cpu.data_out
        )

        if new_req is not None:
            req_type, addr, data = new_req
            self.cache_ctrl.submit_request(req_type, addr, data)
            self._pending_req = {"req_type": req_type, "addr": addr, "start": self.cycle}
        elif not self.cpu.waiting_for_result:
            self.cache_ctrl.clear_request()

        self.memory.tick()

        self.cache_ctrl.mem.ready = self.memory.ready
        if self.memory.ready:
            if self.memory.operation == "read":
                self.cache_ctrl.mem.data_in = list(self.memory.buffer)
            self._mem_op_started = False

        prev_state     = self.cache_ctrl.state
        log_len_before = len(self.cache_ctrl.log)
        self.cache_ctrl.tick()
        curr_state = self.cache_ctrl.state

        if prev_state != curr_state:
            self._mem_op_started = False

        if (curr_state == State.WRITE_BACK
                and not self._mem_op_started and not self.memory.busy):
            self.memory.start_write(self.cache_ctrl.mem.address,
                                    self.cache_ctrl.mem.data_out)
            self._mem_op_started = True
        elif (curr_state == State.ALLOCATE
              and not self._mem_op_started and not self.memory.busy):
            self.memory.start_read(self.cache_ctrl.mem.address)
            self._mem_op_started = True

        # ── memory map block tracking ─────────────────────────────────────────
        bs = self.cache_ctrl.block_size
        if self.cache_ctrl.cpu.valid:
            self._mem_active_block = self.cache_ctrl.cpu.address & ~(bs - 1)
        elif curr_state == State.IDLE:
            self._mem_active_block = None
        if curr_state == State.ALLOCATE and self.memory.ready:
            self._mem_alloc_block = self.cache_ctrl.mem.address & ~(bs - 1)
        if curr_state == State.WRITE_BACK and self.memory.ready:
            self._mem_wb_block = self.cache_ctrl.mem.address & ~(bs - 1)

        for entry in self.cache_ctrl.log[log_len_before:]:
            event   = entry["event"]
            details = entry["details"]
            tag     = "dim"
            if   "HIT"     in event: tag = "hit"
            elif "MISS"    in event: tag = "miss"
            elif "DONE"    in event: tag = "done"
            elif "WAIT"    in event: tag = "warn"
            elif "REQUEST" in event: tag = "info"

            transition = ""
            if entry["prev_state"] != entry["state"]:
                transition = f" [{entry['prev_state']} → {entry['state']}]"

            self._log(f"{event}{transition}  {details}", tag)

        if self.cache_ctrl.cpu.ready and self.cpu.results:
            r = self.cpu.results[-1]
            if r["type"] == RequestType.READ:
                self._log(f"  ↳ CPU received data: 0x{r['data_returned']:02X}", "done")
            else:
                self._log("  ↳ Write complete", "done")

            # ── complete timeline entry ────────────────────────────────────────
            if self._pending_req is not None:
                new_entries = self.cache_ctrl.log[log_len_before:]
                hit = any("HIT" in e["event"] for e in new_entries)
                self._timeline_entries.append({
                    "req_type": self._pending_req["req_type"],
                    "addr":     self._pending_req["addr"],
                    "hit":      hit,
                    "start":    self._pending_req["start"],
                    "end":      self.cycle,
                })
                self._pending_req = None

        self._draw_fsm()
        self._update_cache_table()
        self._update_signals()
        self._update_stats()
        self._update_memory_window()
        self._update_dataflow_window()
        self._update_timeline()

        if self.cpu.is_done() and self.cache_ctrl.state == State.IDLE:
            self._log("All requests completed!", "done")
            if self.running:
                self._toggle_run()

        # ── record this cycle's snapshot ──────────────────────────────────────
        self._history.append(self._take_snapshot())
        self._hist_pos = len(self._history) - 1
        self._update_history_scrubber()

    # ------------------------------------------------------------------
    # Run / pause / reset / speed
    # ------------------------------------------------------------------

    def _toggle_run(self):
        if self.running:
            self.running = False
            self.run_btn.configure(text="▶ Run", fg=GREEN)
        else:
            self.running = True
            self.run_btn.configure(text="⏸ Pause", fg=YELLOW)
            self._auto_step()

    def _auto_step(self):
        if not self.running:
            return
        self._step()
        if self.running:
            self.root.after(self.speed, self._auto_step)

    def _update_speed(self, val):
        self.speed = int(val)

    def _reset(self):
        self.running = False
        self.run_btn.configure(text="▶ Run", fg=GREEN)

        self._create_sim_objects()
        self._init_memory_data()

        if self.request_list:
            self.cpu.load_requests(list(self.request_list))

        # Wipe history, log, memory tracking, and timeline
        self._history          = []
        self._log_items        = []
        self._hist_pos         = -1
        self._mem_active_block = None
        self._mem_alloc_block  = None
        self._mem_wb_block     = None
        self._timeline_entries = []
        self._pending_req      = None

        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)

        self._refresh_all()
        self._log("Simulator reset. Ready.", "info")
        self._update_decomp_label()

        # Snapshot cycle-0 so the user can step back to the very start
        self._history.append(self._take_snapshot())
        self._hist_pos = 0
        self._update_history_scrubber()

    # ------------------------------------------------------------------
    # Associativity / policy combo handlers
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Hit/Miss Timeline
    # ------------------------------------------------------------------

    def _build_timeline_panel(self, parent):
        hdr = tk.Frame(parent, bg=PANEL_BG)
        hdr.pack(fill=tk.X)

        self._panel_header(hdr, "Hit/Miss Timeline", PINK)

        legend = tk.Frame(hdr, bg=PANEL_BG)
        legend.pack(side=tk.RIGHT, padx=12)
        for color, text in [(GREEN, "● Hit"), (RED, "● Miss"), (CYAN, "● In-flight")]:
            tk.Label(legend, text=text, bg=PANEL_BG, fg=color,
                     font=("Consolas", 8)).pack(side=tk.LEFT, padx=6)

        self.timeline_canvas = tk.Canvas(
            parent, bg="#0a0a14", highlightthickness=0, height=82,
        )
        self.timeline_canvas.pack(fill=tk.X, padx=0, pady=(0, 0))
        self.timeline_canvas.bind("<Configure>", lambda _e: self._update_timeline())

    @staticmethod
    def _rrect(canvas, x1, y1, x2, y2, r=5, **kw):
        """Draw a rounded rectangle on canvas."""
        r = min(r, (x2 - x1) // 2, (y2 - y1) // 2)
        pts = [x1+r, y1,  x2-r, y1,
               x2, y1,    x2, y1+r,
               x2, y2-r,  x2, y2,
               x2-r, y2,  x1+r, y2,
               x1, y2,    x1, y2-r,
               x1, y1+r,  x1, y1]
        return canvas.create_polygon(pts, smooth=True, **kw)

    def _update_timeline(self):
        c = self.timeline_canvas
        c.delete("all")

        w = c.winfo_width()  or 600
        h = c.winfo_height() or 82

        LEFT    = 10
        RIGHT   = 10
        BY1     = 14    # block top
        BY2     = 56    # block bottom
        AXIS_Y  = 64
        TICK_H  = 5
        MIN_BW  = 6     # minimum block pixel width

        entries = self._timeline_entries

        if not entries and self._pending_req is None:
            c.create_text(w // 2, h // 2,
                          text="No requests yet — step through a simulation",
                          fill=DIM, font=("Consolas", 9))
            return

        # Total span: at least up to current cycle
        total_end = max(
            (max(e["end"] for e in entries) if entries else 0),
            self.cycle,
            1,
        )

        avail = w - LEFT - RIGHT
        scale = avail / total_end          # pixels per cycle

        # Axis arrow
        c.create_line(LEFT, AXIS_Y, w - RIGHT, AXIS_Y, fill=DIM, width=1)
        c.create_line(w - RIGHT,     AXIS_Y,
                      w - RIGHT - 6, AXIS_Y - 3, fill=DIM, width=1)
        c.create_line(w - RIGHT,     AXIS_Y,
                      w - RIGHT - 6, AXIS_Y + 3, fill=DIM, width=1)

        # Draw completed entries
        prev_tick_x = -999
        for entry in entries:
            x1 = LEFT + entry["start"] * scale
            x2 = LEFT + entry["end"]   * scale
            x2 = max(x2, x1 + MIN_BW)

            fill_color    = "#1a3a1a" if entry["hit"] else "#3a1a1a"
            border_color  = GREEN    if entry["hit"] else RED

            SimulatorGUI._rrect(c, x1, BY1, x2, BY2, r=4,
                                fill=fill_color, outline=border_color, width=1)

            bw = x2 - x1
            rw = "R" if entry["req_type"] == RequestType.READ else "W"
            cy = entry["end"] - entry["start"]
            result = "HIT" if entry["hit"] else "MISS"

            if bw >= 90:
                label = f"{rw} 0x{entry['addr']:04X}\n{result} ({cy} cy)"
            elif bw >= 50:
                label = f"{rw} 0x{entry['addr']:04X}"
            elif bw >= 24:
                label = f"0x{entry['addr']:02X}"
            else:
                label = ""

            if label:
                c.create_text((x1 + x2) / 2, (BY1 + BY2) / 2,
                              text=label, fill=border_color,
                              font=("Consolas", 7), justify=tk.CENTER)

            # Tick at start (suppress if too close to previous tick)
            tx = LEFT + entry["start"] * scale
            if tx - prev_tick_x >= 18:
                c.create_line(tx, AXIS_Y, tx, AXIS_Y + TICK_H, fill=DIM, width=1)
                c.create_text(tx, AXIS_Y + TICK_H + 2,
                              text=str(entry["start"]),
                              fill=DIM, font=("Consolas", 7), anchor=tk.N)
                prev_tick_x = tx

        # Draw in-flight block (dashed cyan)
        if self._pending_req is not None:
            x1 = LEFT + self._pending_req["start"] * scale
            x2 = LEFT + self.cycle * scale
            x2 = max(x2, x1 + MIN_BW)

            SimulatorGUI._rrect(c, x1, BY1, x2, BY2, r=4,
                                fill="#0d1f2d", outline=CYAN, width=1, dash=(4, 2))
            rw = "R" if self._pending_req["req_type"] == RequestType.READ else "W"
            bw = x2 - x1
            if bw >= 24:
                c.create_text((x1 + x2) / 2, (BY1 + BY2) / 2,
                              text=f"{rw} 0x{self._pending_req['addr']:04X}",
                              fill=CYAN, font=("Consolas", 7))

        # Current-cycle tick
        cx = LEFT + self.cycle * scale
        if cx <= w - RIGHT:
            c.create_line(cx, AXIS_Y, cx, AXIS_Y + TICK_H, fill=ACCENT, width=1)
            c.create_text(cx, AXIS_Y + TICK_H + 2,
                          text=str(self.cycle),
                          fill=ACCENT, font=("Consolas", 7), anchor=tk.N)

    # ------------------------------------------------------------------
    # Address decomposition tooltip + live label
    # ------------------------------------------------------------------

    def _decompose_addr(self, addr):
        """Return (tag, index, offset, tag_bits, index_bits, offset_bits)."""
        ctrl = self.cache_ctrl
        t, i, o = ctrl._decompose_address(addr)
        return t, i, o, ctrl.tag_bits, ctrl.index_bits, ctrl.offset_bits

    def _update_decomp_label(self):
        """Refresh the live decomposition hint below the addr entry."""
        try:
            addr = int(self.addr_entry.get().strip(), 16)
        except ValueError:
            self.decomp_label.configure(text="", fg=DIM)
            return

        ctrl = self.cache_ctrl
        tag, idx, off, tb, ib, ob = self._decompose_addr(addr)
        bits   = ctrl.addr_bits
        hi_tag = bits - 1
        lo_tag = ob + ib
        hi_idx = ob + ib - 1
        hi_off = ob - 1

        if ib > 0:
            text = (f"0x{addr:04X}  →  "
                    f"Tag[{hi_tag}:{lo_tag}]=0x{tag:X}  "
                    f"Idx[{hi_idx}:{ob}]={idx}  "
                    f"Off[{hi_off}:0]={off}")
        else:
            text = (f"0x{addr:04X}  →  "
                    f"Tag[{hi_tag}:{ob}]=0x{tag:X}  "
                    f"Off[{hi_off}:0]={off}  (fully assoc — no index)")
        self.decomp_label.configure(text=text, fg=ACCENT)

    def _get_log_addr_at(self, x, y):
        """Return the integer address under mouse position in log_text, or None."""
        import re
        try:
            idx        = self.log_text.index(f"@{x},{y}")
            line_start = self.log_text.index(f"{idx} linestart")
            line_end   = self.log_text.index(f"{idx} lineend")
            line       = self.log_text.get(line_start, line_end)
            col        = int(idx.split(".")[1])
            for m in re.finditer(r'0x[0-9A-Fa-f]+', line):
                if m.start() <= col <= m.end():
                    val = int(m.group(), 16)
                    if val < (1 << self.cache_ctrl.addr_bits):
                        return val
        except Exception:
            pass
        return None

    def _on_log_motion(self, event):
        addr = self._get_log_addr_at(event.x, event.y)
        if addr is None:
            self._hide_addr_tooltip()
        elif addr != self._tooltip_addr:
            self._show_addr_tooltip(addr, event.x_root, event.y_root)

    def _on_log_leave(self, _event=None):
        self._hide_addr_tooltip()

    def _hide_addr_tooltip(self):
        if self._tooltip_win and self._tooltip_win.winfo_exists():
            self._tooltip_win.destroy()
        self._tooltip_win  = None
        self._tooltip_addr = None

    def _show_addr_tooltip(self, addr, x_root, y_root):
        self._hide_addr_tooltip()
        self._tooltip_addr = addr

        ctrl               = self.cache_ctrl
        tag, idx, off, tb, ib, ob = self._decompose_addr(addr)
        bits               = ctrl.addr_bits
        binary             = f"{addr:0{bits}b}"

        # Split raw binary into the three fields
        tag_bin = binary[:tb]
        idx_bin = binary[tb:tb + ib] if ib > 0 else ""
        off_bin = binary[tb + ib:]

        # Block-aligned address and bit-range strings
        block_addr = addr & ~((1 << ob) - 1)
        hi_tag     = bits - 1
        lo_tag     = ob + ib
        hi_idx     = ob + ib - 1
        hi_off     = ob - 1

        # ── build tooltip window ─────────────────────────────────────────
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.configure(bg="#11111b", relief=tk.SOLID, bd=1)
        win.attributes("-topmost", True)
        win.geometry(f"+{x_root + 18}+{y_root + 10}")

        T = tk.Text(win, bg="#11111b", relief=tk.FLAT,
                    font=("Consolas", 9), state=tk.NORMAL,
                    width=48, height=1,   # height set after insert
                    cursor="arrow", takefocus=False,
                    padx=8, pady=6)
        T.pack()

        T.tag_configure("title",  foreground=ACCENT,    font=("Consolas", 9, "bold"))
        T.tag_configure("sep",    foreground="#313244")
        T.tag_configure("lbl",    foreground=DIM,       font=("Consolas", 9))
        T.tag_configure("tag_f",  foreground=CYAN,      font=("Consolas", 9, "bold"))
        T.tag_configure("idx_f",  foreground=YELLOW,    font=("Consolas", 9, "bold"))
        T.tag_configure("off_f",  foreground=GREEN,     font=("Consolas", 9, "bold"))
        T.tag_configure("plain",  foreground=TEXT_COLOR)

        SEP = "─" * 42

        # Line 1 — address + coloured binary
        T.insert(tk.END, f"  0x{addr:04X}  =  ", "title")
        T.insert(tk.END, tag_bin,  "tag_f")
        if ib > 0:
            T.insert(tk.END, " ", "sep")
            T.insert(tk.END, idx_bin, "idx_f")
        T.insert(tk.END, " ", "sep")
        T.insert(tk.END, off_bin,  "off_f")
        T.insert(tk.END, "\n")

        # Line 2 — field-colour legend
        T.insert(tk.END, "  ", "sep")
        T.insert(tk.END, f"{'TAG':^{max(tb,3)}}", "tag_f")
        if ib > 0:
            T.insert(tk.END, " ", "sep")
            T.insert(tk.END, f"{'IX':^{max(ib,2)}}", "idx_f")
        T.insert(tk.END, " ", "sep")
        T.insert(tk.END, f"{'OF':^{max(ob,2)}}", "off_f")
        T.insert(tk.END, "\n")

        # Line 3 — separator
        T.insert(tk.END, f"  {SEP}\n", "sep")

        # Lines 4-6 — numeric breakdown
        T.insert(tk.END, f"  Tag    [{hi_tag:2d}:{lo_tag:2d}]  ", "lbl")
        T.insert(tk.END, f"0b{tag_bin:<{tb}}  =  0x{tag:X}  ({tb}b)\n", "tag_f")

        if ib > 0:
            T.insert(tk.END, f"  Index  [{hi_idx:2d}:{ob:2d}]  ", "lbl")
            T.insert(tk.END, f"0b{idx_bin:<{ib}}  =  {idx}  ({ib}b)\n", "idx_f")

        T.insert(tk.END, f"  Offset [{hi_off:2d}: 0]  ", "lbl")
        T.insert(tk.END, f"0b{off_bin:<{ob}}  =  {off}  ({ob}b)\n", "off_f")

        # Line — separator + result
        T.insert(tk.END, f"  {SEP}\n", "sep")
        T.insert(tk.END, f"  Block: 0x{block_addr:04X}", "plain")
        if ib > 0:
            T.insert(tk.END, f"  ·  Set: {idx}", "plain")
        T.insert(tk.END, "\n")

        # Fix height to content
        lines = int(T.index(tk.END).split(".")[0])
        T.configure(state=tk.DISABLED, height=lines - 1)

        self._tooltip_win = win

        # Keep tooltip alive while mouse stays in the log widget; destroy on focus loss
        win.bind("<Enter>", lambda _e: None)   # don't steal events

    # ------------------------------------------------------------------
    # Import address trace from file
    # ------------------------------------------------------------------

    def _import_trace(self):
        """Load an address trace file (.txt or .csv).

        Supported formats (one request per line):
          R 0x0100          — read hex address
          W 0x0200 0xFF     — write hex address with data
          READ 0x0100       — same, using full keyword
          WRITE 0x0200 FF   — 0x prefix on data is optional
          0x0100            — bare address treated as READ
        Lines starting with # are comments. Blank lines are skipped.
        """
        path = filedialog.askopenfilename(
            title="Import Address Trace",
            filetypes=[
                ("Text files", "*.txt"),
                ("CSV files",  "*.csv"),
                ("All files",  "*.*"),
            ],
        )
        if not path:
            return

        try:
            requests = []
            with open(path, "r", encoding="utf-8") as fh:
                for _, raw_line in enumerate(fh, 1):
                    line = raw_line.strip()
                    if not line or line.startswith("#"):
                        continue

                    parts = line.replace(",", " ").split()
                    if not parts:
                        continue

                    # Determine type and address
                    first = parts[0].upper()
                    if first in ("R", "READ"):
                        rt = RequestType.READ
                        addr_str = parts[1] if len(parts) > 1 else "0"
                        data_str = parts[2] if len(parts) > 2 else "0"
                    elif first in ("W", "WRITE"):
                        rt = RequestType.WRITE
                        addr_str = parts[1] if len(parts) > 1 else "0"
                        data_str = parts[2] if len(parts) > 2 else "0"
                    else:
                        # Bare address — treat as READ
                        rt = RequestType.READ
                        addr_str = first
                        data_str = "0"

                    addr = int(addr_str, 16) if addr_str.startswith("0x") or addr_str.startswith("0X") else int(addr_str, 16)
                    data = int(data_str, 16) if data_str.startswith("0x") or data_str.startswith("0X") else int(data_str, 16)
                    requests.append((rt, addr, data))

            if not requests:
                messagebox.showwarning("Empty Trace",
                                       "No valid requests found in the file.",
                                       parent=self.root)
                return

            self.request_list = requests
            self._reset()
            self._log(f"Imported {len(requests)} requests from {os.path.basename(path)}", "info")

        except Exception as exc:
            messagebox.showerror("Import Error",
                                 f"Failed to parse trace file:\n{exc}",
                                 parent=self.root)

    # ------------------------------------------------------------------
    # Export simulation report
    # ------------------------------------------------------------------

    def _export_report(self):
        """Ask where to save, then write TXT or CSV based on chosen extension."""
        path = filedialog.asksaveasfilename(
            title="Export Simulation Report",
            defaultextension=".txt",
            filetypes=[
                ("Text report",   "*.txt"),
                ("CSV spreadsheet", "*.csv"),
                ("All files",     "*.*"),
            ],
            initialfile="cache_sim_report",
        )
        if not path:
            return

        try:
            if path.lower().endswith(".csv"):
                content = self._build_csv_report()
            else:
                content = self._build_txt_report()

            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(content)

            messagebox.showinfo("Export Complete",
                                f"Report saved to:\n{path}")
        except Exception as exc:
            messagebox.showerror("Export Failed", str(exc))

    # ── TXT report ────────────────────────────────────────────────────

    def _build_txt_report(self):
        ctrl  = self.cache_ctrl
        stats = ctrl.get_stats()
        W     = 80   # page width
        now   = datetime.datetime.now().strftime("%Y-%m-%d  %H:%M:%S")

        def bar(ch="─"):
            return ch * W + "\n"

        def section(title):
            return f"\n{title}\n{bar()}"

        out = io.StringIO()

        # ── header ────────────────────────────────────────────────────
        out.write(bar("═"))
        title = "CACHE CONTROLLER FSM SIMULATOR — SIMULATION REPORT"
        out.write(title.center(W) + "\n")
        out.write(f"Generated: {now}".center(W) + "\n")
        out.write(bar("═"))

        # ── configuration ─────────────────────────────────────────────
        out.write(section("CONFIGURATION"))
        assoc = ctrl.associativity
        if assoc == 1:
            assoc_str = "Direct-Mapped"
            policy_str = "Direct-Mapped"
        else:
            assoc_str  = f"{assoc}-way Set-Associative"
            policy_str = ctrl.policy.value

        out.write(f"  Cache Lines   : {ctrl.num_lines}\n")
        out.write(f"  Block Size    : {ctrl.block_size} words\n")
        out.write(f"  Address Bits  : {ctrl.addr_bits}\n")
        out.write(f"  Associativity : {assoc_str}\n")
        out.write(f"  Sets          : {ctrl.num_sets}\n")
        out.write(f"  Replacement   : {policy_str}\n")
        out.write(f"  Tag Bits      : {ctrl.tag_bits}   "
                  f"Index Bits : {ctrl.index_bits}   "
                  f"Offset Bits : {ctrl.offset_bits}\n")
        out.write(f"  Write Policy  : Write-Back, Write-Allocate\n")

        # ── statistics ────────────────────────────────────────────────
        out.write(section("PERFORMANCE STATISTICS"))
        out.write(f"  Total Requests : {stats['total_requests']}\n")
        misses = stats['misses']
        hits   = stats['hits']
        total  = stats['total_requests'] or 1
        out.write(f"  Cache Hits     : {hits}  ({hits/total*100:.1f}%)\n")
        out.write(f"  Cache Misses   : {misses}  ({misses/total*100:.1f}%)\n")
        out.write(f"    Compulsory   : {stats['compulsory_misses']}\n")
        out.write(f"    Conflict     : {stats['conflict_misses']}\n")
        out.write(f"  Hit Rate       : {stats['hit_rate']}\n")
        out.write(f"  Total Cycles   : {stats['total_cycles']}\n")
        out.write(f"  Stall Cycles   : {stats['stall_cycles']}\n")
        out.write(f"  Bus Reads      : {stats['bus_reads']}  (allocations)\n")
        out.write(f"  Bus Writes     : {stats['bus_writes']}  (write-backs)\n")
        out.write(f"  Avg Miss Pen.  : {stats['avg_miss_penalty']} cycles\n")
        out.write(f"  AMAT           : {stats['amat']} cycles  "
                  f"(Hit Time + Miss Rate x Miss Penalty)\n")

        # ── request timeline ──────────────────────────────────────────
        out.write(section("REQUEST TIMELINE"))
        if self._timeline_entries:
            hdr = f"  {'#':>3}  {'Type':<6}  {'Address':<9}  {'Result':<6}  {'Start':>6}  {'End':>6}  {'Cycles':>7}\n"
            out.write(hdr)
            out.write("  " + "─" * (len(hdr) - 3) + "\n")
            for i, e in enumerate(self._timeline_entries, 1):
                rw     = "READ " if e["req_type"] == RequestType.READ else "WRITE"
                result = "HIT  " if e["hit"] else "MISS "
                cy     = e["end"] - e["start"]
                out.write(f"  {i:>3}  {rw:<6}  "
                          f"0x{e['addr']:04X}     "
                          f"{result:<6}  "
                          f"{e['start']:>6}  {e['end']:>6}  {cy:>7}\n")
        else:
            out.write("  (no requests completed)\n")

        # ── CPU results ───────────────────────────────────────────────
        out.write(section("CPU RESULTS"))
        if self.cpu.results:
            for i, r in enumerate(self.cpu.results):
                if r["type"] == RequestType.READ:
                    out.write(f"  [{i}] READ   addr=0x{r['address']:04X}"
                              f"  =>  data=0x{r['data_returned']:02X}\n")
                else:
                    out.write(f"  [{i}] WRITE  addr=0x{r['address']:04X}"
                              f"  =>  OK\n")
        else:
            out.write("  (no results yet)\n")

        # ── cycle trace ───────────────────────────────────────────────
        out.write(section("CYCLE TRACE"))
        if ctrl.log:
            col_w = [7, 13, 13, 24, 0]   # last col fills remainder
            hdr_cols = ["Cycle", "Prev State", "Curr State", "Event", "Details"]
            row_fmt = ("  {:<{w0}}  {:<{w1}}  {:<{w2}}  {:<{w3}}  {}\n")

            out.write(row_fmt.format(*hdr_cols,
                                     w0=col_w[0], w1=col_w[1],
                                     w2=col_w[2], w3=col_w[3]))
            out.write("  " + "─" * (W - 2) + "\n")
            for entry in ctrl.log:
                out.write(row_fmt.format(
                    entry["cycle"],
                    entry["prev_state"],
                    entry["state"],
                    entry["event"],
                    entry["details"],
                    w0=col_w[0], w1=col_w[1],
                    w2=col_w[2], w3=col_w[3],
                ))
        else:
            out.write("  (no cycles executed)\n")

        # ── final cache state ─────────────────────────────────────────
        out.write(section("FINAL CACHE STATE"))
        snap = ctrl.get_cache_snapshot()
        if ctrl.associativity > 1:
            out.write(f"  {'Set':>4}  {'Way':>4}  {'V':>1}  {'D':>1}  {'Tag':<8}  {'Wr':>4}  {'Block Data'}\n")
            out.write("  " + "─" * 56 + "\n")
            for line in snap:
                v    = "1" if line["valid"] else "0"
                d    = "1" if line["dirty"] else "0"
                tag  = line["tag"] if line["valid"] else "—"
                data = " ".join(line["data"]) if line["valid"] else "(empty)"
                wc   = str(line["write_count"]) if line["write_count"] > 0 else "—"
                out.write(f"  {line['set']:>4}  {line['way']:>4}  {v}  {d}  {tag:<8}  {wc:>4}  {data}\n")
        else:
            out.write(f"  {'Set':>4}  {'V':>1}  {'D':>1}  {'Tag':<8}  {'Wr':>4}  {'Block Data'}\n")
            out.write("  " + "─" * 52 + "\n")
            for line in snap:
                v    = "1" if line["valid"] else "0"
                d    = "1" if line["dirty"] else "0"
                tag  = line["tag"] if line["valid"] else "—"
                data = " ".join(line["data"]) if line["valid"] else "(empty)"
                wc   = str(line["write_count"]) if line["write_count"] > 0 else "—"
                out.write(f"  {line['set']:>4}  {v}  {d}  {tag:<8}  {wc:>4}  {data}\n")

        # ── footer ────────────────────────────────────────────────────
        out.write("\n" + bar("═"))
        out.write("End of Report".center(W) + "\n")
        out.write(bar("═"))

        return out.getvalue()

    # ── CSV report ────────────────────────────────────────────────────

    def _build_csv_report(self):
        ctrl  = self.cache_ctrl
        stats = ctrl.get_stats()
        now   = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        out = io.StringIO()
        w   = csv.writer(out)

        def blank():
            w.writerow([])

        def heading(title):
            blank()
            w.writerow([f"=== {title} ==="])

        # ── metadata ──────────────────────────────────────────────────
        w.writerow(["Cache Controller FSM Simulator — Report"])
        w.writerow(["Generated", now])

        # ── configuration ─────────────────────────────────────────────
        heading("Configuration")
        assoc = ctrl.associativity
        w.writerow(["Cache Lines",   ctrl.num_lines])
        w.writerow(["Block Size",    ctrl.block_size])
        w.writerow(["Address Bits",  ctrl.addr_bits])
        w.writerow(["Associativity", assoc if assoc > 1 else "Direct-Mapped"])
        w.writerow(["Sets",          ctrl.num_sets])
        w.writerow(["Replacement",   ctrl.policy.value])
        w.writerow(["Tag Bits",      ctrl.tag_bits])
        w.writerow(["Index Bits",    ctrl.index_bits])
        w.writerow(["Offset Bits",   ctrl.offset_bits])
        w.writerow(["Write Policy",  "Write-Back Write-Allocate"])

        # ── statistics ────────────────────────────────────────────────
        heading("Performance Statistics")
        w.writerow(["Metric", "Value"])
        w.writerow(["Total Requests",    stats["total_requests"]])
        w.writerow(["Cache Hits",        stats["hits"]])
        w.writerow(["Cache Misses",      stats["misses"]])
        w.writerow(["Compulsory Misses", stats["compulsory_misses"]])
        w.writerow(["Conflict Misses",   stats["conflict_misses"]])
        w.writerow(["Hit Rate",          stats["hit_rate"]])
        w.writerow(["Total Cycles",      stats["total_cycles"]])
        w.writerow(["Stall Cycles",      stats["stall_cycles"]])
        w.writerow(["Bus Reads",         stats["bus_reads"]])
        w.writerow(["Bus Writes",        stats["bus_writes"]])
        w.writerow(["Avg Miss Penalty",  stats["avg_miss_penalty"]])
        w.writerow(["AMAT (cycles)",     stats["amat"]])

        # ── request timeline ──────────────────────────────────────────
        heading("Request Timeline")
        w.writerow(["#", "Type", "Address", "Result",
                    "Start Cycle", "End Cycle", "Cycles Used"])
        for i, e in enumerate(self._timeline_entries, 1):
            w.writerow([
                i,
                "READ" if e["req_type"] == RequestType.READ else "WRITE",
                f"0x{e['addr']:04X}",
                "HIT" if e["hit"] else "MISS",
                e["start"],
                e["end"],
                e["end"] - e["start"],
            ])

        # ── CPU results ───────────────────────────────────────────────
        heading("CPU Results")
        w.writerow(["#", "Type", "Address", "Data Returned"])
        for i, r in enumerate(self.cpu.results):
            data = f"0x{r['data_returned']:02X}" if r["data_returned"] is not None else "—"
            w.writerow([
                i,
                "READ" if r["type"] == RequestType.READ else "WRITE",
                f"0x{r['address']:04X}",
                data,
            ])

        # ── cycle trace ───────────────────────────────────────────────
        heading("Cycle Trace")
        w.writerow(["Cycle", "Prev State", "Curr State", "Event",
                    "Details", "CPU Ready", "CPU Stall", "Mem Read", "Mem Write"])
        for entry in ctrl.log:
            w.writerow([
                entry["cycle"],
                entry["prev_state"],
                entry["state"],
                entry["event"],
                entry["details"],
                entry["cpu_ready"],
                entry["cpu_stall"],
                entry["mem_read"],
                entry["mem_write"],
            ])

        # ── final cache state ─────────────────────────────────────────
        heading("Final Cache State")
        w.writerow(["Set", "Way", "Valid", "Dirty", "Tag", "Write Count", "Data"])
        for line in ctrl.get_cache_snapshot():
            w.writerow([
                line["set"],
                line["way"],
                line["valid"],
                line["dirty"],
                line["tag"] if line["valid"] else "",
                line["write_count"],
                " ".join(line["data"]) if line["valid"] else "",
            ])

        return out.getvalue()

    def _open_memory_window(self):
        """Open (or bring to front) the memory map window."""
        if self._mem_window is None or not self._mem_window.winfo_exists():
            self._mem_window = MemoryWindow(self.root)
        else:
            self._mem_window.lift()
        self._update_memory_window()

    def _update_memory_window(self):
        """Push current memory state to the memory map window if it is open."""
        if self._mem_window is None or not self._mem_window.winfo_exists():
            return
        self._mem_window.update_display(
            self.memory,
            self.cache_ctrl.block_size,
            self._mem_active_block,
            self._mem_alloc_block,
            self._mem_wb_block,
        )

    def _open_dataflow_window(self):
        """Open (or bring to front) the data flow animation window."""
        if self._dataflow_window is None or not self._dataflow_window.winfo_exists():
            self._dataflow_window = DataFlowWindow(self.root)
        else:
            self._dataflow_window.lift()
        self._update_dataflow_window()

    def _update_dataflow_window(self):
        """Push current state to the data flow window if it is open."""
        if self._dataflow_window is None or not self._dataflow_window.winfo_exists():
            return
        self._dataflow_window.update_display(
            self.cache_ctrl, self.memory, self.cpu, self.cycle
        )

    # ------------------------------------------------------------------
    # Settings dialog
    # ------------------------------------------------------------------

    def _open_settings(self):
        """Modal dialog for configuring cache and memory parameters."""
        dlg = tk.Toplevel(self.root)
        dlg.title("Cache Parameters")
        dlg.configure(bg=BG_COLOR)
        dlg.resizable(False, False)
        dlg.grab_set()          # modal
        dlg.transient(self.root)

        # ── valid option lists ─────────────────────────────────────────
        LINE_OPTS  = [2, 4, 8, 16, 32, 64, 128, 256]
        BLOCK_OPTS = [2, 4, 8, 16, 32]
        ADDR_OPTS  = [8, 10, 12, 14, 16, 18, 20, 24]
        LAT_RANGE  = (1, 50)

        # ── local vars (pre-populate from current config) ──────────────
        v_lines  = tk.IntVar(value=self._cfg_num_lines)
        v_block  = tk.IntVar(value=self._cfg_block_size)
        v_addr   = tk.IntVar(value=self._cfg_addr_bits)
        v_rdlat  = tk.IntVar(value=self._cfg_read_lat)
        v_wrlat  = tk.IntVar(value=self._cfg_write_lat)

        def lbl(parent, text, fg=DIM):
            return tk.Label(parent, text=text, bg=PANEL_BG, fg=fg,
                            font=("Consolas", 9))

        def combo(parent, var, opts, width=8):
            cb = ttk.Combobox(parent, textvariable=var,
                              values=opts, state="readonly", width=width)
            cb.bind("<<ComboboxSelected>>", lambda _e: refresh_preview())
            return cb

        def spinbox(parent, var):
            sb = tk.Spinbox(parent, textvariable=var,
                            from_=LAT_RANGE[0], to=LAT_RANGE[1],
                            width=5, bg="#45475a", fg=TEXT_COLOR,
                            font=("Consolas", 9), buttonbackground="#45475a",
                            command=refresh_preview)
            sb.bind("<KeyRelease>", lambda _e: refresh_preview())
            return sb

        # ── layout ─────────────────────────────────────────────────────
        pad = dict(padx=12, pady=4)

        outer = tk.Frame(dlg, bg=BG_COLOR, padx=16, pady=12)
        outer.pack(fill=tk.BOTH, expand=True)

        # Title
        tk.Label(outer, text="Cache Parameters", bg=BG_COLOR, fg=ACCENT,
                 font=("Consolas", 12, "bold")).grid(
                     row=0, column=0, columnspan=3, sticky=tk.W, pady=(0, 8))

        # Section: Cache structure
        sect = tk.Frame(outer, bg=PANEL_BG, padx=10, pady=8)
        sect.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 6))

        tk.Label(sect, text="Cache Structure", bg=PANEL_BG, fg=ACCENT,
                 font=("Consolas", 10, "bold")).grid(
                     row=0, column=0, columnspan=3, sticky=tk.W, pady=(0, 6))

        lbl(sect, "Number of lines").grid(row=1, column=0, sticky=tk.W, **pad)
        combo(sect, v_lines, LINE_OPTS, width=6).grid(row=1, column=1, sticky=tk.W)
        lbl(sect, "(power of 2)").grid(row=1, column=2, sticky=tk.W, padx=(4, 0))

        lbl(sect, "Block size (words)").grid(row=2, column=0, sticky=tk.W, **pad)
        combo(sect, v_block, BLOCK_OPTS, width=6).grid(row=2, column=1, sticky=tk.W)
        lbl(sect, "(power of 2)").grid(row=2, column=2, sticky=tk.W, padx=(4, 0))

        lbl(sect, "Address bits").grid(row=3, column=0, sticky=tk.W, **pad)
        combo(sect, v_addr, ADDR_OPTS, width=6).grid(row=3, column=1, sticky=tk.W)
        lbl(sect, f"(addr space = 2ⁿ bytes)").grid(
            row=3, column=2, sticky=tk.W, padx=(4, 0))

        # Section: Memory latency
        lsect = tk.Frame(outer, bg=PANEL_BG, padx=10, pady=8)
        lsect.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(0, 6))

        tk.Label(lsect, text="Memory Latency", bg=PANEL_BG, fg=ACCENT,
                 font=("Consolas", 10, "bold")).grid(
                     row=0, column=0, columnspan=3, sticky=tk.W, pady=(0, 6))

        lbl(lsect, "Read latency (cycles)").grid(row=1, column=0, sticky=tk.W, **pad)
        spinbox(lsect, v_rdlat).grid(row=1, column=1, sticky=tk.W)

        lbl(lsect, "Write latency (cycles)").grid(row=2, column=0, sticky=tk.W, **pad)
        spinbox(lsect, v_wrlat).grid(row=2, column=1, sticky=tk.W)

        # Section: Live bit-field preview
        prev_frame = tk.Frame(outer, bg=PANEL_BG, padx=10, pady=8)
        prev_frame.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(0, 6))

        tk.Label(prev_frame, text="Address Bit-Field Preview", bg=PANEL_BG, fg=ACCENT,
                 font=("Consolas", 10, "bold")).pack(anchor=tk.W, pady=(0, 4))

        preview_lbl = tk.Label(prev_frame, text="", bg=PANEL_BG, fg=TEXT_COLOR,
                               font=("Consolas", 9), justify=tk.LEFT, anchor=tk.W)
        preview_lbl.pack(fill=tk.X)

        warn_lbl = tk.Label(prev_frame, text="", bg=PANEL_BG, fg=RED,
                            font=("Consolas", 9, "bold"), anchor=tk.W)
        warn_lbl.pack(fill=tk.X)

        def refresh_preview(_e=None):
            try:
                nl  = int(v_lines.get())
                bs  = int(v_block.get())
                ab  = int(v_addr.get())
                assoc = self._current_associativity()
                ns  = max(nl // assoc, 1)

                ob = (bs - 1).bit_length()
                ib = (ns - 1).bit_length() if ns > 1 else 0
                tb = ab - ob - ib

                addr_space = 2 ** ab
                unit = "KB" if addr_space >= 1024 else "B"
                space_val = addr_space // 1024 if addr_space >= 1024 else addr_space

                lines = [
                    f"  Address bits   : {ab}",
                    f"  Addr space     : {space_val} {unit}",
                    f"  Tag bits       : {tb}  [bits {ab-1}:{ob+ib}]",
                    f"  Index bits     : {ib}  [bits {ob+ib-1}:{ob}]" if ib > 0
                    else "  Index bits     : 0  (fully assoc or 1 set)",
                    f"  Offset bits    : {ob}  [bits {ob-1}:0]",
                    f"  Cache size     : {nl * bs} words  ({nl} lines × {bs} words)",
                ]
                preview_lbl.configure(text="\n".join(lines))

                errors = []
                if tb < 1:
                    errors.append("⚠ Too many index/offset bits — tag would be < 1 bit.")
                if nl < assoc:
                    errors.append(f"⚠ Lines ({nl}) < current associativity ({assoc}).")
                warn_lbl.configure(text="\n".join(errors))
                apply_btn.configure(state=tk.NORMAL if not errors else tk.DISABLED)
            except Exception:
                pass

        refresh_preview()

        # ── buttons ────────────────────────────────────────────────────
        btn_row = tk.Frame(outer, bg=BG_COLOR)
        btn_row.grid(row=4, column=0, columnspan=3, pady=(6, 0), sticky="e")

        tk.Label(btn_row,
                 text="⚠ Applying will reset the simulation.",
                 bg=BG_COLOR, fg=YELLOW,
                 font=("Consolas", 8)).pack(side=tk.LEFT, padx=(0, 16))

        tk.Button(btn_row, text="Cancel",
                  command=dlg.destroy,
                  bg="#45475a", fg=DIM,
                  font=("Consolas", 9, "bold"),
                  relief=tk.FLAT, padx=10, pady=4).pack(side=tk.LEFT, padx=(0, 6))

        apply_btn = tk.Button(btn_row, text="Apply & Reset",
                              command=lambda: self._apply_settings(
                                  dlg,
                                  int(v_lines.get()), int(v_block.get()),
                                  int(v_addr.get()),  int(v_rdlat.get()),
                                  int(v_wrlat.get()),
                              ),
                              bg="#45475a", fg=GREEN,
                              font=("Consolas", 9, "bold"),
                              relief=tk.FLAT, padx=10, pady=4)
        apply_btn.pack(side=tk.LEFT)

        # Centre dialog over main window
        dlg.update_idletasks()
        mx = self.root.winfo_x() + (self.root.winfo_width()  - dlg.winfo_width())  // 2
        my = self.root.winfo_y() + (self.root.winfo_height() - dlg.winfo_height()) // 2
        dlg.geometry(f"+{mx}+{my}")

    def _apply_settings(self, dlg, num_lines, block_size, addr_bits,
                        read_lat, write_lat):
        """Validate, store config, close dialog, reset simulation."""
        assoc = self._current_associativity()

        # Hard validation (Apply button should already be disabled on error,
        # but guard here too)
        ob = (block_size - 1).bit_length()
        ns = max(num_lines // assoc, 1)
        ib = (ns - 1).bit_length() if ns > 1 else 0
        tb = addr_bits - ob - ib

        if tb < 1:
            messagebox.showerror(
                "Invalid Configuration",
                "Tag bits < 1.\nReduce block size or increase address bits.",
                parent=dlg)
            return
        if num_lines < assoc:
            messagebox.showerror(
                "Invalid Configuration",
                f"Number of lines ({num_lines}) is less than current "
                f"associativity ({assoc}).\n"
                "Reduce associativity first (in the main window).",
                parent=dlg)
            return

        self._cfg_num_lines  = num_lines
        self._cfg_block_size = block_size
        self._cfg_addr_bits  = addr_bits
        self._cfg_read_lat   = read_lat
        self._cfg_write_lat  = write_lat

        dlg.destroy()
        self._reset()

    def _open_compare(self):
        """Open the side-by-side policy comparison window."""
        CompareWindow(self.root, initial_requests=list(self.request_list))

    # ------------------------------------------------------------------
    # Step-back / replay history
    # ------------------------------------------------------------------

    def _take_snapshot(self):
        """Deep-copy all simulation state into a dict for later restore."""
        return {
            "cache_ctrl":        copy.deepcopy(self.cache_ctrl),
            "memory":            copy.deepcopy(self.memory),
            "cpu":               copy.deepcopy(self.cpu),
            "_mem_op_started":   self._mem_op_started,
            "cycle":             self.cycle,
            "log_items":         list(self._log_items),
            "mem_active_block":  self._mem_active_block,
            "mem_alloc_block":   self._mem_alloc_block,
            "mem_wb_block":      self._mem_wb_block,
            "timeline_entries":  list(self._timeline_entries),  # shallow; dicts are replaced not mutated
            "pending_req":       dict(self._pending_req) if self._pending_req else None,
        }

    def _restore_snapshot(self, snap):
        """Replace live simulation objects with the copies stored in snap."""
        self.cache_ctrl          = snap["cache_ctrl"]
        self.memory              = snap["memory"]
        self.cpu                 = snap["cpu"]
        self._mem_op_started     = snap["_mem_op_started"]
        self.cycle               = snap["cycle"]
        self._log_items          = list(snap["log_items"])
        self._mem_active_block   = snap.get("mem_active_block")
        self._mem_alloc_block    = snap.get("mem_alloc_block")
        self._mem_wb_block       = snap.get("mem_wb_block")
        self._timeline_entries   = list(snap.get("timeline_entries", []))
        self._pending_req        = snap.get("pending_req")

        # Rebuild the log widget from the stored line/tag pairs
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        for line, tag in self._log_items:
            self.log_text.insert(tk.END, line, tag)
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _refresh_after_restore(self):
        """Redraw all panels after a snapshot restore (back or scrub)."""
        self._draw_fsm()
        self._update_cache_table()
        self._update_signals()
        self._update_stats()
        self._update_history_scrubber()
        self._update_memory_window()
        self._update_dataflow_window()
        self._update_timeline()

    def _step_back(self):
        """Go one cycle backward through recorded history."""
        if self._hist_pos <= 0:
            return
        self._hist_pos -= 1
        self._restore_snapshot(self._history[self._hist_pos])
        self._refresh_after_restore()

    def _update_history_scrubber(self):
        """Sync the scrubber range, thumb position, label, and Back button."""
        max_pos = max(0, len(self._history) - 1)
        self._scrubbing = True          # suppress _on_scrub re-entry
        self.hist_scale.configure(to=max_pos)
        self.hist_scale.set(self._hist_pos)
        self._scrubbing = False
        self.hist_pos_lbl.configure(text=f"cycle {self._hist_pos} / {max_pos}")
        self.back_btn.configure(
            state=tk.NORMAL if self._hist_pos > 0 else tk.DISABLED)

    def _on_scrub(self, val):
        """Called when the user drags the history scrubber."""
        if self._scrubbing:
            return
        pos = int(float(val))
        if pos == self._hist_pos or not (0 <= pos < len(self._history)):
            return
        self._hist_pos = pos
        self._restore_snapshot(self._history[pos])
        self._refresh_after_restore()

    def _on_assoc_change(self, _event=None):
        assoc = self._current_associativity()
        if assoc == 1:
            self.policy_combo.configure(state="disabled")
        else:
            self.policy_combo.configure(state="readonly")
        # Auto-reset so the cache structure matches the new config
        self._reset()

    # ------------------------------------------------------------------
    # Request management
    # ------------------------------------------------------------------

    def _add_request(self):
        try:
            addr = int(self.addr_entry.get(), 16)
        except ValueError:
            messagebox.showerror("Invalid Address",
                                 "Enter a valid hex address (e.g. 0100)")
            return
        try:
            data = int(self.data_entry.get(), 16)
        except ValueError:
            messagebox.showerror("Invalid Data",
                                 "Enter a valid hex value (e.g. FF)")
            return

        rt = (RequestType.READ if self.req_type_var.get() == "READ"
              else RequestType.WRITE)
        self.request_list.append((rt, addr, data))
        self.cpu.add_request(rt, addr, data)
        self._update_queue_display()

        t = "READ" if rt == RequestType.READ else "WRITE"
        self._log(f"Added: {t} addr=0x{addr:04X} data=0x{data:02X}", "info")

    def _load_preset(self, event=None):
        name = self.preset_var.get()
        if not name or name not in self.preset_requests:
            return

        self.request_list = list(self.preset_requests[name])
        self._reset()
        self._log(f"Loaded preset: {name}", "info")


def main():
    root = tk.Tk()
    app  = SimulatorGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
