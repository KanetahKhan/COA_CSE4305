#!/usr/bin/env python3
"""
Cache Controller FSM Simulator — GUI Version
=============================================
Tkinter-based visual simulator showing:
  - FSM state diagram with live transitions
  - Cache table with valid/dirty/tag/data
  - CPU and Memory interface signals
  - Cycle-by-cycle stepping or auto-run
  - Request queue management
  - Event log
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

import math
from cache_controller import CacheController, State, RequestType
from memory import Memory
from cpu import CPU


STATE_COLORS = {
    State.IDLE:        "#4CAF50",
    State.COMPARE_TAG: "#2196F3",
    State.WRITE_BACK:  "#F44336",
    State.ALLOCATE:    "#FF9800",
}

STATE_POSITIONS = {
    State.IDLE:        (150, 60),
    State.COMPARE_TAG: (150, 180),
    State.ALLOCATE:    (300, 300),
    State.WRITE_BACK:  (0, 300),
}

BG_COLOR = "#1e1e2e"
PANEL_BG = "#2a2a3d"
TEXT_COLOR = "#cdd6f4"
ACCENT = "#89b4fa"
GREEN = "#a6e3a1"
RED = "#f38ba8"
YELLOW = "#f9e2af"
CYAN = "#89dceb"
DIM = "#6c7086"


class SimulatorGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Cache Controller FSM Simulator")
        self.root.configure(bg=BG_COLOR)
        self.root.geometry("1280x820")
        self.root.minsize(1100, 750)

        self.cache_ctrl = CacheController(num_lines=8, block_size=4, addr_bits=16)
        self.memory = Memory(size=65536, block_size=4, read_latency=3, write_latency=2)
        self.cpu = CPU()
        self._mem_op_started = False

        self.cycle = 0
        self.running = False
        self.speed = 500

        self.request_list = []
        self.preset_requests = {
            "Read Miss → Hit": [
                (RequestType.READ, 0x0100, 0),
                (RequestType.READ, 0x0101, 0),
            ],
            "Write-Allocate": [
                (RequestType.WRITE, 0x0202, 0xFF),
                (RequestType.READ, 0x0202, 0),
                (RequestType.READ, 0x0200, 0),
            ],
            "Dirty Eviction": [
                (RequestType.WRITE, 0x0001, 0xEE),
                (RequestType.READ, 0x0100, 0),
            ],
            "Clean Eviction": [
                (RequestType.READ, 0x0000, 0),
                (RequestType.READ, 0x0100, 0),
            ],
            "Write + Readback": [
                (RequestType.WRITE, 0x0000, 0xA1),
                (RequestType.WRITE, 0x0004, 0xB2),
                (RequestType.WRITE, 0x0008, 0xC3),
                (RequestType.READ, 0x0000, 0),
                (RequestType.READ, 0x0004, 0),
                (RequestType.READ, 0x0008, 0),
            ],
            "Stress Test": [
                (RequestType.WRITE, 0x0000, 0xF0),
                (RequestType.WRITE, 0x0100, 0xF1),
                (RequestType.READ, 0x0200, 0),
                (RequestType.READ, 0x0000, 0),
                (RequestType.WRITE, 0x0000, 0xAA),
                (RequestType.READ, 0x0100, 0),
            ],
        }

        self._init_memory_data()
        self._build_ui()
        self._draw_fsm()
        self._update_cache_table()
        self._update_signals()

    def _init_memory_data(self):
        self.memory.init_region(0x0000, [0x11, 0x22, 0x33, 0x44])
        self.memory.init_region(0x0100, [0xAA, 0xBB, 0xCC, 0xDD])
        self.memory.init_region(0x0200, [0x10, 0x20, 0x30, 0x40])

    def _build_ui(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Dark.TFrame", background=BG_COLOR)
        style.configure("Panel.TFrame", background=PANEL_BG)
        style.configure("Dark.TLabel", background=BG_COLOR, foreground=TEXT_COLOR,
                        font=("Consolas", 10))
        style.configure("Title.TLabel", background=PANEL_BG, foreground=ACCENT,
                        font=("Consolas", 11, "bold"))
        style.configure("Dark.TButton", font=("Consolas", 10))
        style.configure("Dark.TCombobox", font=("Consolas", 10))

        main = ttk.Frame(self.root, style="Dark.TFrame")
        main.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        top_frame = ttk.Frame(main, style="Dark.TFrame")
        top_frame.pack(fill=tk.X, pady=(0, 6))

        left_top = ttk.Frame(top_frame, style="Panel.TFrame")
        left_top.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 4))
        self._build_fsm_panel(left_top)

        right_top = ttk.Frame(top_frame, style="Dark.TFrame")
        right_top.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(4, 0))

        self._build_signals_panel(right_top)
        self._build_controls_panel(right_top)

        bottom_frame = ttk.Frame(main, style="Dark.TFrame")
        bottom_frame.pack(fill=tk.BOTH, expand=True)

        left_bot = ttk.Frame(bottom_frame, style="Dark.TFrame")
        left_bot.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 4))
        self._build_cache_panel(left_bot)

        right_bot = ttk.Frame(bottom_frame, style="Dark.TFrame")
        right_bot.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(4, 0))

        self._build_request_panel(right_bot)
        self._build_log_panel(right_bot)

    def _build_fsm_panel(self, parent):
        ttk.Label(parent, text=" FSM State Diagram", style="Title.TLabel").pack(
            anchor=tk.W, padx=8, pady=(8, 2))
        self.fsm_canvas = tk.Canvas(parent, bg="#1a1a2e", highlightthickness=0,
                                     height=380, width=420)
        self.fsm_canvas.pack(fill=tk.BOTH, expand=True, padx=8, pady=(2, 8))

    def _build_signals_panel(self, parent):
        frame = ttk.Frame(parent, style="Panel.TFrame")
        frame.pack(fill=tk.X, pady=(0, 4))

        ttk.Label(frame, text=" Interface Signals", style="Title.TLabel").pack(
            anchor=tk.W, padx=8, pady=(8, 4))

        sig_frame = ttk.Frame(frame, style="Panel.TFrame")
        sig_frame.pack(fill=tk.X, padx=8, pady=(0, 8))

        self.signal_labels = {}
        cpu_signals = ["cpu_valid", "cpu_rd_wr", "cpu_addr", "cpu_data",
                       "cpu_ready", "cpu_stall"]
        mem_signals = ["mem_read", "mem_write", "mem_addr", "mem_ready"]

        col1 = ttk.Frame(sig_frame, style="Panel.TFrame")
        col1.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(col1, text="CPU ↔ Cache", bg=PANEL_BG, fg=CYAN,
                 font=("Consolas", 9, "bold")).pack(anchor=tk.W)
        for sig in cpu_signals:
            lbl = tk.Label(col1, text=f"  {sig}: —", bg=PANEL_BG, fg=DIM,
                           font=("Consolas", 9), anchor=tk.W)
            lbl.pack(anchor=tk.W)
            self.signal_labels[sig] = lbl

        col2 = ttk.Frame(sig_frame, style="Panel.TFrame")
        col2.pack(side=tk.RIGHT, fill=tk.X, expand=True)
        tk.Label(col2, text="Cache ↔ Memory", bg=PANEL_BG, fg=YELLOW,
                 font=("Consolas", 9, "bold")).pack(anchor=tk.W)
        for sig in mem_signals:
            lbl = tk.Label(col2, text=f"  {sig}: —", bg=PANEL_BG, fg=DIM,
                           font=("Consolas", 9), anchor=tk.W)
            lbl.pack(anchor=tk.W)
            self.signal_labels[sig] = lbl

    def _build_controls_panel(self, parent):
        frame = ttk.Frame(parent, style="Panel.TFrame")
        frame.pack(fill=tk.X, pady=(4, 0))

        ttk.Label(frame, text=" Controls", style="Title.TLabel").pack(
            anchor=tk.W, padx=8, pady=(8, 4))

        row1 = ttk.Frame(frame, style="Panel.TFrame")
        row1.pack(fill=tk.X, padx=8, pady=2)

        self.cycle_label = tk.Label(row1, text="Cycle: 0", bg=PANEL_BG, fg=ACCENT,
                                     font=("Consolas", 14, "bold"))
        self.cycle_label.pack(side=tk.LEFT, padx=(0, 16))

        self.stats_label = tk.Label(row1, text="Hits: 0 | Misses: 0 | Rate: —",
                                     bg=PANEL_BG, fg=TEXT_COLOR, font=("Consolas", 10))
        self.stats_label.pack(side=tk.LEFT)

        row2 = ttk.Frame(frame, style="Panel.TFrame")
        row2.pack(fill=tk.X, padx=8, pady=(4, 4))

        self.step_btn = tk.Button(row2, text="⏵ Step", command=self._step,
                                   bg="#45475a", fg=TEXT_COLOR, font=("Consolas", 10, "bold"),
                                   relief=tk.FLAT, padx=12, pady=4)
        self.step_btn.pack(side=tk.LEFT, padx=(0, 4))

        self.run_btn = tk.Button(row2, text="▶ Run", command=self._toggle_run,
                                  bg="#45475a", fg=GREEN, font=("Consolas", 10, "bold"),
                                  relief=tk.FLAT, padx=12, pady=4)
        self.run_btn.pack(side=tk.LEFT, padx=(0, 4))

        self.reset_btn = tk.Button(row2, text="↺ Reset", command=self._reset,
                                    bg="#45475a", fg=RED, font=("Consolas", 10, "bold"),
                                    relief=tk.FLAT, padx=12, pady=4)
        self.reset_btn.pack(side=tk.LEFT, padx=(0, 12))

        tk.Label(row2, text="Speed:", bg=PANEL_BG, fg=DIM,
                 font=("Consolas", 9)).pack(side=tk.LEFT)
        self.speed_scale = tk.Scale(row2, from_=50, to=1000, orient=tk.HORIZONTAL,
                                     bg=PANEL_BG, fg=TEXT_COLOR, troughcolor="#45475a",
                                     highlightthickness=0, font=("Consolas", 8),
                                     length=120, command=self._update_speed)
        self.speed_scale.set(500)
        self.speed_scale.pack(side=tk.LEFT, padx=4)

        row3 = ttk.Frame(frame, style="Panel.TFrame")
        row3.pack(fill=tk.X, padx=8, pady=(0, 8))

        tk.Label(row3, text="Preset:", bg=PANEL_BG, fg=DIM,
                 font=("Consolas", 9)).pack(side=tk.LEFT)
        self.preset_var = tk.StringVar()
        preset_combo = ttk.Combobox(row3, textvariable=self.preset_var,
                                     values=list(self.preset_requests.keys()),
                                     state="readonly", width=20)
        preset_combo.pack(side=tk.LEFT, padx=4)
        preset_combo.bind("<<ComboboxSelected>>", self._load_preset)

        self.load_btn = tk.Button(row3, text="Load", command=self._load_preset,
                                   bg="#45475a", fg=TEXT_COLOR, font=("Consolas", 9),
                                   relief=tk.FLAT, padx=8)
        self.load_btn.pack(side=tk.LEFT, padx=2)

    def _build_cache_panel(self, parent):
        frame = ttk.Frame(parent, style="Panel.TFrame")
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text=" Cache Contents", style="Title.TLabel").pack(
            anchor=tk.W, padx=8, pady=(8, 4))

        cols = ("Index", "Valid", "Dirty", "Tag", "Block Data")
        self.cache_tree = ttk.Treeview(frame, columns=cols, show="headings", height=8)

        style = ttk.Style()
        style.configure("Treeview", background="#1a1a2e", foreground=TEXT_COLOR,
                        fieldbackground="#1a1a2e", font=("Consolas", 10), rowheight=25)
        style.configure("Treeview.Heading", background="#45475a", foreground=TEXT_COLOR,
                        font=("Consolas", 10, "bold"))
        style.map("Treeview", background=[("selected", "#45475a")])

        widths = [50, 50, 50, 80, 280]
        for col, w in zip(cols, widths):
            self.cache_tree.heading(col, text=col)
            self.cache_tree.column(col, width=w, anchor=tk.CENTER)

        self.cache_tree.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

    def _build_request_panel(self, parent):
        frame = ttk.Frame(parent, style="Panel.TFrame")
        frame.pack(fill=tk.X, pady=(0, 4))

        ttk.Label(frame, text=" Add Request", style="Title.TLabel").pack(
            anchor=tk.W, padx=8, pady=(8, 4))

        row = ttk.Frame(frame, style="Panel.TFrame")
        row.pack(fill=tk.X, padx=8, pady=(0, 4))

        self.req_type_var = tk.StringVar(value="READ")
        tk.Radiobutton(row, text="READ", variable=self.req_type_var, value="READ",
                       bg=PANEL_BG, fg=GREEN, selectcolor=PANEL_BG,
                       font=("Consolas", 9), activebackground=PANEL_BG,
                       activeforeground=GREEN).pack(side=tk.LEFT)
        tk.Radiobutton(row, text="WRITE", variable=self.req_type_var, value="WRITE",
                       bg=PANEL_BG, fg=RED, selectcolor=PANEL_BG,
                       font=("Consolas", 9), activebackground=PANEL_BG,
                       activeforeground=RED).pack(side=tk.LEFT, padx=(8, 0))

        row2 = ttk.Frame(frame, style="Panel.TFrame")
        row2.pack(fill=tk.X, padx=8, pady=(0, 4))

        tk.Label(row2, text="Addr 0x", bg=PANEL_BG, fg=DIM,
                 font=("Consolas", 9)).pack(side=tk.LEFT)
        self.addr_entry = tk.Entry(row2, width=6, bg="#45475a", fg=TEXT_COLOR,
                                    font=("Consolas", 10), insertbackground=TEXT_COLOR)
        self.addr_entry.insert(0, "0000")
        self.addr_entry.pack(side=tk.LEFT, padx=4)

        tk.Label(row2, text="Data 0x", bg=PANEL_BG, fg=DIM,
                 font=("Consolas", 9)).pack(side=tk.LEFT, padx=(8, 0))
        self.data_entry = tk.Entry(row2, width=4, bg="#45475a", fg=TEXT_COLOR,
                                    font=("Consolas", 10), insertbackground=TEXT_COLOR)
        self.data_entry.insert(0, "00")
        self.data_entry.pack(side=tk.LEFT, padx=4)

        self.add_btn = tk.Button(row2, text="+ Add", command=self._add_request,
                                  bg="#45475a", fg=GREEN, font=("Consolas", 9, "bold"),
                                  relief=tk.FLAT, padx=8)
        self.add_btn.pack(side=tk.LEFT, padx=4)

        self.queue_label = tk.Label(frame, text="Queue: (empty)", bg=PANEL_BG, fg=DIM,
                                     font=("Consolas", 9), anchor=tk.W, wraplength=500,
                                     justify=tk.LEFT)
        self.queue_label.pack(fill=tk.X, padx=8, pady=(0, 8))

    def _build_log_panel(self, parent):
        frame = ttk.Frame(parent, style="Panel.TFrame")
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text=" Event Log", style="Title.TLabel").pack(
            anchor=tk.W, padx=8, pady=(8, 2))

        self.log_text = scrolledtext.ScrolledText(
            frame, bg="#1a1a2e", fg=TEXT_COLOR, font=("Consolas", 9),
            height=10, insertbackground=TEXT_COLOR, wrap=tk.WORD,
            relief=tk.FLAT, state=tk.DISABLED
        )
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=8, pady=(2, 8))

        self.log_text.tag_configure("hit", foreground=GREEN)
        self.log_text.tag_configure("miss", foreground=RED)
        self.log_text.tag_configure("info", foreground=CYAN)
        self.log_text.tag_configure("warn", foreground=YELLOW)
        self.log_text.tag_configure("done", foreground=GREEN)
        self.log_text.tag_configure("dim", foreground=DIM)

    def _draw_fsm(self):
        c = self.fsm_canvas
        c.delete("all")

        w = c.winfo_width() or 420
        h = c.winfo_height() or 380

        ox = w * 0.5
        oy = h * 0.5

        positions = {
            State.IDLE:        (ox, oy - 130),
            State.COMPARE_TAG: (ox, oy - 20),
            State.WRITE_BACK:  (ox - 120, oy + 110),
            State.ALLOCATE:    (ox + 120, oy + 110),
        }

        R = 42
        current = self.cache_ctrl.state

        arrows = [
            (State.IDLE, State.COMPARE_TAG, "CPU valid", 10),
            (State.COMPARE_TAG, State.WRITE_BACK, "Miss\n(dirty)", -20),
            (State.COMPARE_TAG, State.ALLOCATE, "Miss\n(clean)", 20),
            (State.WRITE_BACK, State.ALLOCATE, "Mem\nready", 0),
            (State.ALLOCATE, State.IDLE, "Mem\nready", 10),
        ]

        for src, dst, label, offset in arrows:
            x1, y1 = positions[src]
            x2, y2 = positions[dst]

            dx = x2 - x1
            dy = y2 - y1
            dist = math.sqrt(dx*dx + dy*dy)
            if dist == 0:
                continue
            ux, uy = dx/dist, dy/dist

            sx = x1 + ux * (R + 4)
            sy = y1 + uy * (R + 4)
            ex = x2 - ux * (R + 4)
            ey = y2 - uy * (R + 4)

            is_active = (current == dst and self.cache_ctrl.prev_state == src)
            color = "#f9e2af" if is_active else "#585b70"
            width = 3 if is_active else 1.5

            px = -uy * offset
            py = ux * offset
            mx = (sx + ex) / 2 + px
            my = (sy + ey) / 2 + py

            c.create_line(sx, sy, mx, my, ex, ey, smooth=True,
                         fill=color, width=width, arrow=tk.LAST, arrowshape=(10, 12, 5))

            lx = mx + (-uy) * 18
            ly = my + ux * 18
            c.create_text(lx, ly, text=label, fill=color,
                         font=("Consolas", 7), justify=tk.CENTER)

        hit_x, hit_y = positions[State.COMPARE_TAG]
        is_hit = (current == State.IDLE and
                  self.cache_ctrl.prev_state == State.COMPARE_TAG and
                  self.cycle > 0)
        hit_color = "#a6e3a1" if is_hit else "#585b70"
        hit_w = 3 if is_hit else 1.5

        c.create_line(hit_x + R + 4, hit_y - 10,
                     hit_x + R + 40, hit_y - 30,
                     positions[State.IDLE][0] + R + 4, positions[State.IDLE][1] + 10,
                     smooth=True, fill=hit_color, width=hit_w,
                     arrow=tk.LAST, arrowshape=(10, 12, 5))
        c.create_text(hit_x + R + 50, hit_y - 30, text="Hit!", fill=hit_color,
                     font=("Consolas", 8, "bold"))

        for state, (x, y) in positions.items():
            is_current = (state == current)
            fill = STATE_COLORS[state] if is_current else "#313244"
            outline = STATE_COLORS[state]
            width = 3 if is_current else 1.5

            c.create_oval(x - R, y - R, x + R, y + R,
                         fill=fill, outline=outline, width=width)

            text_color = "#1e1e2e" if is_current else TEXT_COLOR
            c.create_text(x, y, text=state.value, fill=text_color,
                         font=("Consolas", 9, "bold"))

            if is_current:
                c.create_oval(x - R - 5, y - R - 5, x + R + 5, y + R + 5,
                             outline=STATE_COLORS[state], width=1, dash=(3, 3))

    def _update_cache_table(self):
        for item in self.cache_tree.get_children():
            self.cache_tree.delete(item)

        for line in self.cache_ctrl.get_cache_snapshot():
            v = "1" if line["valid"] else "0"
            d = "1" if line["dirty"] else "0"
            data = " ".join(line["data"])
            tag = "—" if v == "0" else line["tag"]
            self.cache_tree.insert("", tk.END, values=(
                line["index"], v, d, tag, data
            ))

    def _update_signals(self):
        ctrl = self.cache_ctrl

        def set_sig(name, value, active=False):
            color = GREEN if active else DIM
            self.signal_labels[name].configure(text=f"  {name}: {value}", fg=color)

        set_sig("cpu_valid", "1" if ctrl.cpu.valid else "0", ctrl.cpu.valid)
        rw = "—"
        if ctrl.cpu.read_write == RequestType.READ:
            rw = "READ"
        elif ctrl.cpu.read_write == RequestType.WRITE:
            rw = "WRITE"
        set_sig("cpu_rd_wr", rw, ctrl.cpu.valid)
        set_sig("cpu_addr", f"0x{ctrl.cpu.address:04X}" if ctrl.cpu.valid else "—",
                ctrl.cpu.valid)
        set_sig("cpu_data", f"0x{ctrl.cpu.data_out:02X}" if ctrl.cpu.ready else "—",
                ctrl.cpu.ready)
        set_sig("cpu_ready", "1" if ctrl.cpu.ready else "0", ctrl.cpu.ready)
        set_sig("cpu_stall", "1" if ctrl.cpu.stall else "0", ctrl.cpu.stall)

        set_sig("mem_read", "1" if ctrl.mem.read else "0", ctrl.mem.read)
        set_sig("mem_write", "1" if ctrl.mem.write else "0", ctrl.mem.write)
        set_sig("mem_addr", f"0x{ctrl.mem.address:04X}" if (ctrl.mem.read or ctrl.mem.write) else "—",
                ctrl.mem.read or ctrl.mem.write)
        set_sig("mem_ready", "1" if ctrl.mem.ready else "0", ctrl.mem.ready)

    def _update_stats(self):
        stats = self.cache_ctrl.get_stats()
        self.cycle_label.configure(text=f"Cycle: {self.cycle}")
        rate = stats["hit_rate"] if stats["total_requests"] else "—"
        self.stats_label.configure(
            text=f"Hits: {stats['hits']} | Misses: {stats['misses']} | Rate: {rate}")

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

    def _log(self, message, tag="dim"):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"[{self.cycle:>4}] {message}\n", tag)
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _step(self):
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
        elif not self.cpu.waiting_for_result:
            self.cache_ctrl.clear_request()

        self.memory.tick()

        self.cache_ctrl.mem.ready = self.memory.ready
        if self.memory.ready:
            if self.memory.operation == "read":
                self.cache_ctrl.mem.data_in = list(self.memory.buffer)
            self._mem_op_started = False

        prev_state = self.cache_ctrl.state
        log_len_before = len(self.cache_ctrl.log)
        self.cache_ctrl.tick()
        curr_state = self.cache_ctrl.state

        if prev_state != curr_state:
            self._mem_op_started = False

        if curr_state == State.WRITE_BACK and not self._mem_op_started and not self.memory.busy:
            self.memory.start_write(self.cache_ctrl.mem.address,
                                    self.cache_ctrl.mem.data_out)
            self._mem_op_started = True
        elif curr_state == State.ALLOCATE and not self._mem_op_started and not self.memory.busy:
            self.memory.start_read(self.cache_ctrl.mem.address)
            self._mem_op_started = True

        for entry in self.cache_ctrl.log[log_len_before:]:
            event = entry["event"]
            details = entry["details"]
            tag = "dim"
            if "HIT" in event:
                tag = "hit"
            elif "MISS" in event:
                tag = "miss"
            elif "DONE" in event:
                tag = "done"
            elif "WAIT" in event:
                tag = "warn"
            elif "REQUEST" in event:
                tag = "info"

            transition = ""
            if entry["prev_state"] != entry["state"]:
                transition = f" [{entry['prev_state']} → {entry['state']}]"

            self._log(f"{event}{transition}  {details}", tag)

        if self.cache_ctrl.cpu.ready and self.cpu.results:
            r = self.cpu.results[-1]
            if r["type"] == RequestType.READ:
                self._log(f"  ↳ CPU received data: 0x{r['data_returned']:02X}", "done")
            else:
                self._log(f"  ↳ Write complete", "done")

        self._draw_fsm()
        self._update_cache_table()
        self._update_signals()
        self._update_stats()

        if self.cpu.is_done() and self.cache_ctrl.state == State.IDLE:
            self._log("All requests completed!", "done")
            if self.running:
                self._toggle_run()

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

        self.cache_ctrl = CacheController(num_lines=8, block_size=4, addr_bits=16)
        self.memory = Memory(size=65536, block_size=4, read_latency=3, write_latency=2)
        self.cpu = CPU()
        self._mem_op_started = False
        self.cycle = 0

        self._init_memory_data()

        if self.request_list:
            self.cpu.load_requests(list(self.request_list))

        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)

        self._draw_fsm()
        self._update_cache_table()
        self._update_signals()
        self._update_stats()
        self._update_queue_display()
        self._log("Simulator reset. Ready.", "info")

    def _add_request(self):
        try:
            addr = int(self.addr_entry.get(), 16)
        except ValueError:
            messagebox.showerror("Invalid Address", "Enter a valid hex address (e.g. 0100)")
            return
        try:
            data = int(self.data_entry.get(), 16)
        except ValueError:
            messagebox.showerror("Invalid Data", "Enter a valid hex value (e.g. FF)")
            return

        rt = RequestType.READ if self.req_type_var.get() == "READ" else RequestType.WRITE
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
    app = SimulatorGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()python gyui.py