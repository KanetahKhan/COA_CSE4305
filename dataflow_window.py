#!/usr/bin/env python3
"""
Data Flow Animation Window
===========================
Animated architectural block diagram showing data packets flowing
between CPU, Cache Controller, and Memory in real time as the
simulation steps forward.

  CPU  ←—32-bit—→  Cache Controller  ←—128-bit—→  Memory

Packet colours:
  CYAN   — address from CPU to cache
  GREEN  — read data (cache→CPU or memory→cache)
  RED    — write data / confirmation
  ORANGE — write-back (cache→memory dirty block)
"""

import tkinter as tk
from tkinter import ttk
import sys
import os
import math

sys.path.insert(0, os.path.dirname(__file__))

from cache_controller import State, RequestType

# ── colour palette (Catppuccin Mocha) ─────────────────────────────────────────
BG_COLOR   = "#1e1e2e"
PANEL_BG   = "#2a2a3d"
CANVAS_BG  = "#0d0d1a"
TEXT_COLOR  = "#cdd6f4"
ACCENT     = "#89b4fa"
GREEN      = "#a6e3a1"
RED        = "#f38ba8"
YELLOW     = "#f9e2af"
CYAN       = "#89dceb"
ORANGE     = "#fab387"
DIM        = "#6c7086"
DARK       = "#313244"
PURPLE     = "#cba6f7"
TEAL       = "#94e2d5"
PINK       = "#f5c2e7"

STATE_COLORS = {
    State.IDLE:        GREEN,
    State.COMPARE_TAG: ACCENT,
    State.WRITE_BACK:  RED,
    State.ALLOCATE:    ORANGE,
}

# Component border colours
CPU_COLOR   = CYAN
CACHE_COLOR = ACCENT
MEM_COLOR   = ORANGE


class DataFlowWindow(tk.Toplevel):
    """Floating animated data-flow diagram that updates each simulation step."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Data Flow Animation")
        self.configure(bg=BG_COLOR)
        self.geometry("920x540")
        self.minsize(750, 420)
        self.resizable(True, True)

        # Cached simulation state (set by update_display)
        self._state       = State.IDLE
        self._prev_state  = State.IDLE
        self._cycle       = 0
        self._req_type    = None       # RequestType or None
        self._address     = 0
        self._data_in     = 0          # CPU write data
        self._data_out    = 0          # CPU read data
        self._cpu_valid   = False
        self._cpu_ready   = False
        self._cpu_stall   = False
        self._mem_busy    = False
        self._mem_ready   = False
        self._mem_read    = False
        self._mem_write   = False
        self._mem_counter = 0
        self._mem_read_lat  = 3
        self._mem_write_lat = 2
        self._mem_addr    = 0
        self._mem_data_out = []        # block being written back
        self._mem_data_in  = []        # block being allocated
        self._block_size  = 4
        self._addr_bits   = 16

        # Flash state (for single-cycle events)
        self._flash_items = []
        self._flash_id    = None

        # Previous size (to avoid redundant redraws)
        self._last_w = 0
        self._last_h = 0

        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        root_frame = tk.Frame(self, bg=BG_COLOR)
        root_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # Title bar
        title_bar = tk.Frame(root_frame, bg=PANEL_BG)
        title_bar.pack(fill=tk.X, pady=(0, 6))
        tk.Frame(title_bar, width=3, bg=ACCENT).pack(side=tk.LEFT, fill=tk.Y)
        tk.Label(title_bar, text="  Data Flow Animation", bg=PANEL_BG, fg=ACCENT,
                 font=("Consolas", 11, "bold")).pack(side=tk.LEFT, pady=(8, 6))
        tk.Label(title_bar, text="CPU ←32b→ Cache ←128b→ Memory",
                 bg=PANEL_BG, fg=DIM,
                 font=("Consolas", 9)).pack(side=tk.RIGHT, padx=12, pady=(8, 6))

        # Main canvas
        self._canvas = tk.Canvas(root_frame, bg=CANVAS_BG, highlightthickness=0)
        self._canvas.pack(fill=tk.BOTH, expand=True)
        self._canvas.bind("<Configure>", self._on_configure)

        # Legend
        legend = tk.Frame(root_frame, bg=PANEL_BG)
        legend.pack(fill=tk.X, pady=(6, 0))
        for colour, text in [
            (CYAN,   "■ Address"),
            (GREEN,  "■ Read data"),
            (RED,    "■ Write data"),
            (ORANGE, "■ Write-back"),
            (TEAL,   "■ Allocate"),
        ]:
            tk.Label(legend, text=text, bg=PANEL_BG, fg=colour,
                     font=("Consolas", 8)).pack(side=tk.LEFT, padx=8, pady=4)

    def _on_configure(self, _event=None):
        w = self._canvas.winfo_width()
        h = self._canvas.winfo_height()
        if abs(w - self._last_w) < 3 and abs(h - self._last_h) < 3:
            return
        self._last_w = w
        self._last_h = h
        self._draw()

    # ── public update API ─────────────────────────────────────────────────────

    def update_display(self, cache_ctrl, memory, cpu, cycle):
        """Called by the main GUI after every simulation step."""
        self._prev_state   = cache_ctrl.prev_state
        self._state        = cache_ctrl.state
        self._cycle        = cycle
        self._req_type     = cache_ctrl.saved_request
        self._address      = cache_ctrl.saved_address
        self._data_in      = cache_ctrl.saved_data
        self._data_out     = cache_ctrl.cpu.data_out
        self._cpu_valid    = cache_ctrl.cpu.valid
        self._cpu_ready    = cache_ctrl.cpu.ready
        self._cpu_stall    = cache_ctrl.cpu.stall
        self._mem_busy     = memory.busy
        self._mem_ready    = memory.ready
        self._mem_read     = cache_ctrl.mem.read
        self._mem_write    = cache_ctrl.mem.write
        self._mem_counter  = memory.counter
        self._mem_read_lat = memory.read_latency
        self._mem_write_lat = memory.write_latency
        self._mem_addr     = cache_ctrl.mem.address
        self._mem_data_out = list(cache_ctrl.mem.data_out)
        self._mem_data_in  = list(cache_ctrl.mem.data_in)
        self._block_size   = cache_ctrl.block_size
        self._addr_bits    = cache_ctrl.addr_bits

        # Cancel pending flash
        if self._flash_id is not None:
            try:
                self.after_cancel(self._flash_id)
            except Exception:
                pass
            self._flash_id = None

        self._draw()

    # ── layout helpers ────────────────────────────────────────────────────────

    def _layout(self):
        """Compute layout rectangles scaled to current canvas size."""
        w = max(self._canvas.winfo_width(), 600)
        h = max(self._canvas.winfo_height(), 340)

        # Component block proportions
        bw_side = w * 0.18          # CPU / Memory block width
        bw_mid  = w * 0.26          # Cache block width
        bh      = h * 0.40          # block height
        gap     = w * 0.06          # gap between blocks
        y_top   = h * 0.12
        y_bot   = y_top + bh

        # CPU
        cpu_x1 = w * 0.04
        cpu_x2 = cpu_x1 + bw_side

        # Cache
        cache_x1 = cpu_x2 + gap
        cache_x2 = cache_x1 + bw_mid

        # Memory
        mem_x1 = cache_x2 + gap
        mem_x2 = mem_x1 + bw_side

        # Bus Y positions (upper = address, lower = data)
        bus_y_addr = y_top + bh * 0.32
        bus_y_data = y_top + bh * 0.60

        # Info bar region
        info_y = y_bot + h * 0.10

        return {
            "w": w, "h": h,
            "cpu":   (cpu_x1,   y_top, cpu_x2,   y_bot),
            "cache": (cache_x1, y_top, cache_x2, y_bot),
            "mem":   (mem_x1,   y_top, mem_x2,   y_bot),
            "bus_cc_x": (cpu_x2, cache_x1),       # CPU-Cache bus endpoints
            "bus_cm_x": (cache_x2, mem_x1),        # Cache-Memory bus endpoints
            "bus_y_addr": bus_y_addr,
            "bus_y_data": bus_y_data,
            "info_y": info_y,
        }

    # ── drawing ───────────────────────────────────────────────────────────────

    def _draw(self):
        c = self._canvas
        c.delete("all")

        L = self._layout()
        w, h = L["w"], L["h"]

        # Dot grid background
        for xi in range(14, int(w), 22):
            for yi in range(14, int(h), 22):
                c.create_oval(xi - 1, yi - 1, xi + 1, yi + 1,
                              fill="#1a1a2c", outline="")

        # Draw bus lines (behind everything)
        self._draw_buses(c, L)

        # Draw component blocks
        self._draw_component(c, L["cpu"],   "CPU",              CPU_COLOR,   L)
        self._draw_component(c, L["cache"], "CACHE CONTROLLER", CACHE_COLOR, L)
        self._draw_component(c, L["mem"],   "MEMORY",           MEM_COLOR,   L)

        # Draw component details
        self._draw_cpu_details(c, L)
        self._draw_cache_details(c, L)
        self._draw_memory_details(c, L)

        # Draw data packets
        self._draw_packets(c, L)

        # Draw info bar at bottom
        self._draw_info_bar(c, L)

    @staticmethod
    def _rrect(canvas, x1, y1, x2, y2, r=8, **kw):
        r = min(r, (x2 - x1) / 2, (y2 - y1) / 2)
        pts = [x1+r, y1,  x2-r, y1,
               x2, y1,    x2, y1+r,
               x2, y2-r,  x2, y2,
               x2-r, y2,  x1+r, y2,
               x1, y2,    x1, y2-r,
               x1, y1+r,  x1, y1]
        return canvas.create_polygon(pts, smooth=True, **kw)

    def _draw_component(self, c, rect, title, border_color, L):
        x1, y1, x2, y2 = rect
        is_active = self._is_component_active(title)

        # Glow for active component
        if is_active:
            for ring_pad, alpha in [(10, 0.12), (6, 0.22), (3, 0.35)]:
                sr, sg, sb = int(border_color[1:3], 16), int(border_color[3:5], 16), int(border_color[5:7], 16)
                br, bg_, bb = 0x0d, 0x0d, 0x1a
                mr = int(sr * alpha + br * (1 - alpha))
                mg = int(sg * alpha + bg_ * (1 - alpha))
                mb = int(sb * alpha + bb * (1 - alpha))
                gc = f"#{mr:02x}{mg:02x}{mb:02x}"
                self._rrect(c, x1 - ring_pad, y1 - ring_pad,
                            x2 + ring_pad, y2 + ring_pad, r=10,
                            fill="", outline=gc, width=2)

        # Main block
        fill = "#1a1a2e" if not is_active else "#1e2030"
        bw = 2.5 if is_active else 1.5
        self._rrect(c, x1, y1, x2, y2, r=8,
                    fill=fill, outline=border_color, width=bw)

        # Title
        cx = (x1 + x2) / 2
        c.create_text(cx, y1 + 18, text=title, fill=border_color,
                      font=("Consolas", 9, "bold"))

        # Separator line under title
        c.create_line(x1 + 10, y1 + 30, x2 - 10, y1 + 30,
                      fill=DARK, width=1)

    def _is_component_active(self, component_name):
        s = self._state
        if component_name == "CPU":
            return s == State.IDLE and self._cpu_valid
        elif component_name == "CACHE CONTROLLER":
            return s in (State.COMPARE_TAG, State.WRITE_BACK, State.ALLOCATE)
        else:  # MEMORY
            return s in (State.WRITE_BACK, State.ALLOCATE) and self._mem_busy

    # ── bus lines ─────────────────────────────────────────────────────────────

    def _draw_buses(self, c, L):
        cc_x = L["bus_cc_x"]
        cm_x = L["bus_cm_x"]
        ya = L["bus_y_addr"]
        yd = L["bus_y_data"]

        s = self._state

        # CPU ↔ Cache address bus
        cc_addr_active = s == State.COMPARE_TAG or (s == State.IDLE and self._cpu_valid)
        self._draw_bus_line(c, cc_x[0], cc_x[1], ya,
                            active=cc_addr_active, color=CYAN, label="addr")

        # CPU ↔ Cache data bus
        cc_data_active = self._cpu_ready
        cc_data_color = GREEN if self._req_type == RequestType.READ else RED
        self._draw_bus_line(c, cc_x[0], cc_x[1], yd,
                            active=cc_data_active, color=cc_data_color, label="32-bit data")

        # Cache ↔ Memory address bus
        cm_addr_active = s in (State.WRITE_BACK, State.ALLOCATE)
        self._draw_bus_line(c, cm_x[0], cm_x[1], ya,
                            active=cm_addr_active, color=CYAN, label="addr")

        # Cache ↔ Memory data bus
        cm_data_active = self._mem_read or self._mem_write
        cm_data_color = ORANGE if self._mem_write else TEAL
        bs = self._block_size
        self._draw_bus_line(c, cm_x[0], cm_x[1], yd,
                            active=cm_data_active, color=cm_data_color,
                            label=f"{bs*8*4}-bit data", wide=True)

    def _draw_bus_line(self, c, x1, x2, y, active=False, color=DIM,
                       label="", wide=False):
        lw = 3 if wide else 2
        if active:
            # Glow under the line
            c.create_line(x1, y, x2, y, fill=self._dim_color(color, 0.25),
                          width=lw + 6, capstyle=tk.ROUND)
            c.create_line(x1, y, x2, y, fill=color, width=lw,
                          capstyle=tk.ROUND)
        else:
            c.create_line(x1, y, x2, y, fill=DARK, width=lw,
                          capstyle=tk.ROUND, dash=(4, 4))

        # Bus label
        mx = (x1 + x2) / 2
        c.create_text(mx, y - 10, text=label, fill=DIM if not active else color,
                      font=("Consolas", 7))

    @staticmethod
    def _dim_color(hex_color, alpha):
        r = int(hex_color[1:3], 16)
        g = int(hex_color[3:5], 16)
        b = int(hex_color[5:7], 16)
        br, bg_, bb = 0x0d, 0x0d, 0x1a
        mr = int(r * alpha + br * (1 - alpha))
        mg = int(g * alpha + bg_ * (1 - alpha))
        mb = int(b * alpha + bb * (1 - alpha))
        return f"#{mr:02x}{mg:02x}{mb:02x}"

    # ── component details ─────────────────────────────────────────────────────

    def _draw_cpu_details(self, c, L):
        x1, y1, x2, y2 = L["cpu"]
        cx = (x1 + x2) / 2
        y = y1 + 44

        if self._cpu_valid or self._cpu_ready:
            rw = "READ" if self._req_type == RequestType.READ else "WRITE"
            rw_color = GREEN if self._req_type == RequestType.READ else RED
            c.create_text(cx, y, text=rw, fill=rw_color,
                          font=("Consolas", 10, "bold"))
            y += 18
            c.create_text(cx, y, text=f"0x{self._address:04X}",
                          fill=TEXT_COLOR, font=("Consolas", 10))
            y += 16
            if self._cpu_ready:
                if self._req_type == RequestType.READ:
                    c.create_text(cx, y, text=f"Got: 0x{self._data_out:02X}",
                                  fill=GREEN, font=("Consolas", 9))
                else:
                    c.create_text(cx, y, text="Done",
                                  fill=GREEN, font=("Consolas", 9, "bold"))
            elif self._cpu_stall:
                c.create_text(cx, y, text="STALLED",
                              fill=YELLOW, font=("Consolas", 9, "bold"))
            elif self._req_type == RequestType.WRITE:
                c.create_text(cx, y, text=f"Data: 0x{self._data_in:02X}",
                              fill=DIM, font=("Consolas", 9))

            # Signal indicators
            y += 20
            for sig, val, col in [
                ("valid", self._cpu_valid, GREEN),
                ("ready", self._cpu_ready, GREEN),
                ("stall", self._cpu_stall, YELLOW),
            ]:
                dot_c = col if val else "#2a2a3e"
                c.create_oval(cx - 30, y - 4, cx - 22, y + 4, fill=dot_c, outline="")
                c.create_text(cx + 4, y, text=sig, fill=col if val else DIM,
                              font=("Consolas", 7), anchor=tk.W)
                y += 13
        else:
            c.create_text(cx, y + 20, text="IDLE", fill=DIM,
                          font=("Consolas", 11))
            c.create_text(cx, y + 38, text="waiting...", fill=DIM,
                          font=("Consolas", 8))

    def _draw_cache_details(self, c, L):
        x1, y1, x2, y2 = L["cache"]
        cx = (x1 + x2) / 2
        y = y1 + 42

        # FSM State pill
        sc = STATE_COLORS.get(self._state, DIM)
        state_name = self._state.value
        tw = len(state_name) * 7 + 16
        self._rrect(c, cx - tw/2, y - 10, cx + tw/2, y + 10, r=5,
                    fill=self._dim_color(sc, 0.3), outline=sc, width=1.5)
        c.create_text(cx, y, text=state_name, fill=sc,
                      font=("Consolas", 10, "bold"))

        y += 24

        # Transition arrow if state changed
        if self._prev_state != self._state:
            prev_c = STATE_COLORS.get(self._prev_state, DIM)
            c.create_text(cx, y,
                          text=f"{self._prev_state.value} → {self._state.value}",
                          fill=YELLOW, font=("Consolas", 8))
            y += 16
        else:
            y += 4

        # Address decomposition (when processing a request)
        if self._state != State.IDLE or self._cpu_valid:
            addr = self._address
            c.create_text(cx, y, text=f"Addr: 0x{addr:04X}",
                          fill=TEXT_COLOR, font=("Consolas", 9))
            y += 16

            # Tag / Index / Offset bits
            ab = self._addr_bits
            bs = self._block_size
            ob = (bs - 1).bit_length()
            binary = f"{addr:0{ab}b}"
            # Show first few bits as colored fields
            c.create_text(cx - 40, y, text="T:", fill=CYAN, font=("Consolas", 7))
            c.create_text(cx - 10, y, text="I:", fill=YELLOW, font=("Consolas", 7))
            c.create_text(cx + 18, y, text="O:", fill=GREEN, font=("Consolas", 7))
            y += 14

        # What's happening
        y = y2 - 40
        event_text, event_color = self._get_event_description()
        if event_text:
            c.create_text(cx, y, text=event_text, fill=event_color,
                          font=("Consolas", 8), width=(x2 - x1) - 20,
                          justify=tk.CENTER)

    def _get_event_description(self):
        s = self._state
        ps = self._prev_state

        if s == State.IDLE and ps == State.COMPARE_TAG:
            return "HIT — data delivered", GREEN
        if s == State.IDLE and ps == State.ALLOCATE:
            return "Block loaded — done", GREEN
        if s == State.COMPARE_TAG and ps == State.IDLE:
            return "Comparing tag...", ACCENT
        if s == State.COMPARE_TAG and ps == State.COMPARE_TAG:
            return "Comparing tag...", ACCENT
        if s == State.WRITE_BACK and ps == State.COMPARE_TAG:
            return "MISS (dirty) — writing back", RED
        if s == State.ALLOCATE and ps == State.COMPARE_TAG:
            return "MISS (clean) — fetching block", ORANGE
        if s == State.WRITE_BACK:
            return f"Writing back to memory...", RED
        if s == State.ALLOCATE and ps == State.WRITE_BACK:
            return "Write-back done — fetching", ORANGE
        if s == State.ALLOCATE:
            return "Fetching block from memory...", ORANGE
        return "", DIM

    def _draw_memory_details(self, c, L):
        x1, y1, x2, y2 = L["mem"]
        cx = (x1 + x2) / 2
        y = y1 + 44

        if self._mem_busy or self._mem_ready:
            # Operation type
            op = "READ" if not self._mem_write else "WRITE"
            op_color = TEAL if not self._mem_write else ORANGE
            c.create_text(cx, y, text=op, fill=op_color,
                          font=("Consolas", 10, "bold"))
            y += 18

            # Address
            c.create_text(cx, y, text=f"0x{self._mem_addr:04X}",
                          fill=TEXT_COLOR, font=("Consolas", 10))
            y += 20

            # Progress bar
            if self._mem_busy:
                lat = self._mem_write_lat if self._mem_write else self._mem_read_lat
                progress = min(self._mem_counter / max(lat, 1), 1.0)
                bar_w = (x2 - x1) - 40
                bar_h = 12
                bx1 = cx - bar_w / 2
                by1 = y
                # Track
                self._rrect(c, bx1, by1, bx1 + bar_w, by1 + bar_h, r=3,
                            fill=DARK, outline="")
                # Fill
                if progress > 0:
                    fill_w = bar_w * progress
                    self._rrect(c, bx1, by1, bx1 + fill_w, by1 + bar_h, r=3,
                                fill=op_color, outline="")
                # Counter text
                c.create_text(cx, by1 + bar_h + 12,
                              text=f"{self._mem_counter}/{lat} cycles",
                              fill=DIM, font=("Consolas", 8))
                y += bar_h + 26
            elif self._mem_ready:
                c.create_text(cx, y, text="READY",
                              fill=GREEN, font=("Consolas", 10, "bold"))
                y += 20

            # Signal dots
            for sig, val, col in [
                ("read",  self._mem_read,  TEAL),
                ("write", self._mem_write, ORANGE),
                ("ready", self._mem_ready, GREEN),
            ]:
                dot_c = col if val else "#2a2a3e"
                c.create_oval(cx - 30, y - 4, cx - 22, y + 4,
                              fill=dot_c, outline="")
                c.create_text(cx + 4, y, text=sig,
                              fill=col if val else DIM,
                              font=("Consolas", 7), anchor=tk.W)
                y += 13
        else:
            c.create_text(cx, y + 20, text="IDLE", fill=DIM,
                          font=("Consolas", 11))
            c.create_text(cx, y + 38, text="ready", fill=DIM,
                          font=("Consolas", 8))

    # ── data packets ──────────────────────────────────────────────────────────

    def _draw_packets(self, c, L):
        """Draw animated data packets on the bus lines."""
        s = self._state
        ps = self._prev_state
        cc_x = L["bus_cc_x"]
        cm_x = L["bus_cm_x"]
        ya = L["bus_y_addr"]
        yd = L["bus_y_data"]

        # ── CPU → Cache: address packet (on request received) ────────────
        if s == State.COMPARE_TAG and ps == State.IDLE:
            self._draw_packet(c, cc_x[0], cc_x[1], ya, 1.0,
                              CYAN, f"0x{self._address:04X}",
                              direction="right")

        # ── Cache → CPU: data response on HIT ────────────────────────────
        if s == State.IDLE and ps == State.COMPARE_TAG and self._cpu_ready:
            if self._req_type == RequestType.READ:
                self._draw_packet(c, cc_x[1], cc_x[0], yd, 1.0,
                                  GREEN, f"0x{self._data_out:02X}",
                                  direction="left")
            else:
                self._draw_packet(c, cc_x[1], cc_x[0], yd, 1.0,
                                  RED, "ACK",
                                  direction="left")

        # ── Cache → CPU: data response after ALLOCATE ────────────────────
        if s == State.IDLE and ps == State.ALLOCATE and self._cpu_ready:
            if self._req_type == RequestType.READ:
                self._draw_packet(c, cc_x[1], cc_x[0], yd, 1.0,
                                  GREEN, f"0x{self._data_out:02X}",
                                  direction="left")
            else:
                self._draw_packet(c, cc_x[1], cc_x[0], yd, 1.0,
                                  RED, "ACK",
                                  direction="left")

        # ── WRITE_BACK: Cache → Memory block ─────────────────────────────
        if s == State.WRITE_BACK and self._mem_busy:
            lat = max(self._mem_write_lat, 1)
            progress = min(self._mem_counter / lat, 1.0)
            data_label = self._block_hex_short(self._mem_data_out)
            self._draw_packet(c, cm_x[0], cm_x[1], yd, progress,
                              ORANGE, data_label,
                              direction="right", wide=True)
            # Address on the address bus
            self._draw_packet(c, cm_x[0], cm_x[1], ya, 1.0,
                              CYAN, f"0x{self._mem_addr:04X}",
                              direction="right")

        # ── WRITE_BACK done → show completion flash ──────────────────────
        if s == State.ALLOCATE and ps == State.WRITE_BACK:
            self._draw_packet(c, cm_x[0], cm_x[1], yd, 1.0,
                              ORANGE, "DONE",
                              direction="right", wide=True)

        # ── ALLOCATE: Memory → Cache block ───────────────────────────────
        if s == State.ALLOCATE and self._mem_busy:
            lat = max(self._mem_read_lat, 1)
            progress = min(self._mem_counter / lat, 1.0)
            data_label = self._block_hex_short(self._mem_data_in)
            self._draw_packet(c, cm_x[1], cm_x[0], yd, progress,
                              TEAL, data_label,
                              direction="left", wide=True)
            # Address on the address bus
            self._draw_packet(c, cm_x[0], cm_x[1], ya, 1.0,
                              CYAN, f"0x{self._mem_addr:04X}",
                              direction="right")

        # ── ALLOCATE done (memory ready, state still ALLOCATE) ───────────
        if s == State.ALLOCATE and self._mem_ready:
            data_label = self._block_hex_short(self._mem_data_in)
            self._draw_packet(c, cm_x[1], cm_x[0], yd, 1.0,
                              TEAL, data_label,
                              direction="left", wide=True)

    def _draw_packet(self, c, x_start, x_end, y, progress, color, label,
                     direction="right", wide=False):
        """Draw a single data packet at a position along a bus."""
        # Interpolate x position
        x = x_start + (x_end - x_start) * progress
        pw = 56 if wide else 42
        ph = 18

        # Packet glow
        c.create_oval(x - pw/2 - 4, y - ph/2 - 4,
                      x + pw/2 + 4, y + ph/2 + 4,
                      fill=self._dim_color(color, 0.15), outline="")

        # Packet body
        self._rrect(c, x - pw/2, y - ph/2, x + pw/2, y + ph/2, r=4,
                    fill=self._dim_color(color, 0.5), outline=color, width=1.5)

        # Label
        c.create_text(x, y, text=label, fill=TEXT_COLOR,
                      font=("Consolas", 7, "bold"))

        # Direction arrow
        arrow_x = x + pw/2 + 6 if direction == "right" else x - pw/2 - 6
        if direction == "right":
            c.create_text(arrow_x, y, text="►", fill=color,
                          font=("Consolas", 8))
        else:
            c.create_text(arrow_x, y, text="◄", fill=color,
                          font=("Consolas", 8))

    def _block_hex_short(self, data):
        """Format a block as a compact hex string."""
        if not data:
            return "..."
        if len(data) <= 2:
            return " ".join(f"{d:02X}" for d in data)
        return f"{data[0]:02X}..{data[-1]:02X}"

    # ── info bar ──────────────────────────────────────────────────────────────

    def _draw_info_bar(self, c, L):
        y = L["info_y"]
        w = L["w"]
        h = L["h"]

        # Background bar
        c.create_rectangle(0, y - 4, w, h, fill="#0f0f1a", outline="")
        c.create_line(0, y - 4, w, y - 4, fill=DARK, width=1)

        # Cycle
        c.create_text(20, y + 14, text=f"Cycle: {self._cycle}",
                      fill=ACCENT, font=("Consolas", 11, "bold"), anchor=tk.W)

        # State transition
        sc = STATE_COLORS.get(self._state, DIM)
        if self._prev_state != self._state:
            psc = STATE_COLORS.get(self._prev_state, DIM)
            c.create_text(w * 0.25, y + 14,
                          text=f"{self._prev_state.value}",
                          fill=psc, font=("Consolas", 9), anchor=tk.W)
            c.create_text(w * 0.25 + len(self._prev_state.value) * 7 + 8, y + 14,
                          text="→", fill=YELLOW, font=("Consolas", 9), anchor=tk.W)
            c.create_text(w * 0.25 + len(self._prev_state.value) * 7 + 22, y + 14,
                          text=f"{self._state.value}",
                          fill=sc, font=("Consolas", 9, "bold"), anchor=tk.W)
        else:
            c.create_text(w * 0.25, y + 14,
                          text=f"State: {self._state.value}",
                          fill=sc, font=("Consolas", 9, "bold"), anchor=tk.W)

        # Address
        if self._state != State.IDLE or self._cpu_valid:
            c.create_text(w * 0.55, y + 14,
                          text=f"Address: 0x{self._address:04X}",
                          fill=TEXT_COLOR, font=("Consolas", 9), anchor=tk.W)

        # Active signals summary
        sigs = []
        if self._cpu_valid:   sigs.append(("VALID", CYAN))
        if self._cpu_ready:   sigs.append(("READY", GREEN))
        if self._cpu_stall:   sigs.append(("STALL", YELLOW))
        if self._mem_read:    sigs.append(("MEM_RD", TEAL))
        if self._mem_write:   sigs.append(("MEM_WR", ORANGE))
        if self._mem_ready:   sigs.append(("MEM_RDY", GREEN))

        sx = w * 0.78
        for sig_text, sig_color in sigs:
            c.create_text(sx, y + 14, text=sig_text, fill=sig_color,
                          font=("Consolas", 7, "bold"), anchor=tk.W)
            sx += len(sig_text) * 6 + 10

        # Bottom row: data summary
        y2 = y + 34
        if self._state == State.WRITE_BACK and self._mem_data_out:
            block = " ".join(f"{d:02X}" for d in self._mem_data_out)
            c.create_text(20, y2, text=f"Write-back block: [{block}]",
                          fill=ORANGE, font=("Consolas", 8), anchor=tk.W)
        elif self._state == State.ALLOCATE and self._mem_data_in:
            block = " ".join(f"{d:02X}" for d in self._mem_data_in)
            c.create_text(20, y2, text=f"Allocate block: [{block}]",
                          fill=TEAL, font=("Consolas", 8), anchor=tk.W)
        elif self._cpu_ready and self._req_type == RequestType.READ:
            c.create_text(20, y2,
                          text=f"CPU received: 0x{self._data_out:02X}",
                          fill=GREEN, font=("Consolas", 8), anchor=tk.W)


# ── standalone entry point ────────────────────────────────────────────────────

def main():
    root = tk.Tk()
    root.withdraw()
    win = DataFlowWindow(root)
    win.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()


if __name__ == "__main__":
    main()
