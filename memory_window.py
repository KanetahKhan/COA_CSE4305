#!/usr/bin/env python3
"""
Memory Map Visualization Window
================================
Scrollable hex-dump of simulator memory with live colour-coded highlighting:

  CYAN   — block the CPU is currently accessing (active FSM request)
  GREEN  — last block fetched from memory (ALLOCATE / cache fill)
  ORANGE — last block written back to memory (WRITE_BACK / dirty eviction)
  DIM    — non-zero bytes that have no special status
  DARK   — zero bytes

The window opens from the main GUI and updates itself after every cycle step.
"""

import tkinter as tk
from tkinter import ttk
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

# ── colour palette ─────────────────────────────────────────────────────────────
BG_COLOR   = "#1e1e2e"
PANEL_BG   = "#2a2a3d"
TEXT_COLOR = "#cdd6f4"
ACCENT     = "#89b4fa"
GREEN      = "#a6e3a1"
RED        = "#f38ba8"
YELLOW     = "#f9e2af"
CYAN       = "#89dceb"
ORANGE     = "#fab387"
DIM        = "#6c7086"
DARK       = "#313244"

BPR = 16   # bytes per display row (must be a multiple of block_size)


class MemoryWindow(tk.Toplevel):
    """Floating hex-dump window that receives display updates from the main GUI."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Memory Map")
        self.configure(bg=BG_COLOR)
        self.geometry("780x540")
        self.minsize(640, 400)
        self.resizable(True, True)

        # State set by update_display()
        self._memory     = None
        self._block_size = 4
        self._active     = None   # CPU block address (CYAN)
        self._alloc      = None   # last allocated block (GREEN)
        self._wb         = None   # last write-back block (ORANGE)

        # Range / view options
        self._show_all   = tk.BooleanVar(value=False)
        self._auto_scroll = tk.BooleanVar(value=True)

        self._build_ui()

    # ── UI construction ────────────────────────────────────────────────────────

    def _build_ui(self):
        s = ttk.Style(self)
        s.configure("Mem.TFrame",  background=BG_COLOR)
        s.configure("MemP.TFrame", background=PANEL_BG)

        root_frame = ttk.Frame(self, style="Mem.TFrame")
        root_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # ── toolbar ────────────────────────────────────────────────────────────
        toolbar = ttk.Frame(root_frame, style="MemP.TFrame")
        toolbar.pack(fill=tk.X, pady=(0, 6))

        tk.Label(toolbar, text="  Memory Map", bg=PANEL_BG, fg=ACCENT,
                 font=("Consolas", 11, "bold")).pack(side=tk.LEFT, padx=(0, 16))

        tk.Label(toolbar, text="View:", bg=PANEL_BG, fg=DIM,
                 font=("Consolas", 9)).pack(side=tk.LEFT)

        self._range_var = tk.StringVar(value="Smart")
        for label in ("Smart", "1 KB", "4 KB", "Full"):
            tk.Radiobutton(
                toolbar, text=label, variable=self._range_var, value=label,
                bg=PANEL_BG, fg=TEXT_COLOR, selectcolor=PANEL_BG,
                font=("Consolas", 9), activebackground=PANEL_BG,
                command=self._on_range_change,
            ).pack(side=tk.LEFT, padx=2)

        tk.Checkbutton(
            toolbar, text="Auto-scroll", variable=self._auto_scroll,
            bg=PANEL_BG, fg=DIM, selectcolor=PANEL_BG,
            font=("Consolas", 9), activebackground=PANEL_BG,
        ).pack(side=tk.RIGHT, padx=8)

        # ── hex dump text widget ───────────────────────────────────────────────
        dump_frame = ttk.Frame(root_frame, style="Mem.TFrame")
        dump_frame.pack(fill=tk.BOTH, expand=True)

        self._hex = tk.Text(
            dump_frame,
            bg="#0d0d1a", fg=DIM,
            font=("Consolas", 10),
            relief=tk.FLAT,
            state=tk.DISABLED,
            wrap=tk.NONE,
            cursor="arrow",
        )
        vsb = ttk.Scrollbar(dump_frame, orient=tk.VERTICAL,   command=self._hex.yview)
        hsb = ttk.Scrollbar(dump_frame, orient=tk.HORIZONTAL, command=self._hex.xview)
        self._hex.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        hsb.pack(side=tk.BOTTOM, fill=tk.X)
        vsb.pack(side=tk.RIGHT,  fill=tk.Y)
        self._hex.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Text colour tags
        self._hex.tag_configure("addr",    foreground=DIM,        font=("Consolas", 10))
        self._hex.tag_configure("sep",     foreground=DARK,       font=("Consolas", 10))
        self._hex.tag_configure("active",  foreground=CYAN,       font=("Consolas", 10, "bold"))
        self._hex.tag_configure("alloc",   foreground=GREEN,      font=("Consolas", 10, "bold"))
        self._hex.tag_configure("wb",      foreground=ORANGE,     font=("Consolas", 10, "bold"))
        self._hex.tag_configure("nonzero", foreground=TEXT_COLOR, font=("Consolas", 10))
        self._hex.tag_configure("zero",    foreground=DARK,       font=("Consolas", 10))
        self._hex.tag_configure("ellipsis",foreground=DIM,        font=("Consolas", 9, "italic"))

        # ── legend ─────────────────────────────────────────────────────────────
        legend = ttk.Frame(root_frame, style="MemP.TFrame")
        legend.pack(fill=tk.X, pady=(6, 0))

        for colour, text in [
            (CYAN,   "■ Active (CPU request)"),
            (GREEN,  "■ Allocated (cache fill)"),
            (ORANGE, "■ Write-back (dirty evict)"),
            (TEXT_COLOR, "■ Non-zero"),
            (DARK,   "■ Zero"),
        ]:
            tk.Label(legend, text=text, bg=PANEL_BG, fg=colour,
                     font=("Consolas", 8)).pack(side=tk.LEFT, padx=6, pady=4)

    # ── public update API ──────────────────────────────────────────────────────

    def update_display(self, memory, block_size, active, alloc, wb):
        """Called by the main GUI after every cycle step or restore."""
        self._memory     = memory
        self._block_size = block_size
        self._active     = active
        self._alloc      = alloc
        self._wb         = wb
        self._render()

    # ── rendering ─────────────────────────────────────────────────────────────

    def _on_range_change(self):
        if self._memory:
            self._render()

    def _end_addr(self):
        rng = self._range_var.get()
        if rng == "Smart":
            return None          # special: computed per-render
        if rng == "1 KB":
            return 0x03FF
        if rng == "4 KB":
            return 0x0FFF
        return len(self._memory.data) - 1   # Full

    def _highlighted_blocks(self):
        """Set of block-aligned addresses that must always be visible."""
        out = set()
        for a in (self._active, self._alloc, self._wb):
            if a is not None:
                out.add(a & ~(self._block_size - 1))
        return out

    def _row_is_interesting(self, row_addr, hi_blocks):
        """True if the row contains non-zero bytes or a highlighted block."""
        if self._memory is None:
            return False
        bs   = self._block_size
        data = self._memory.data
        for i in range(BPR):
            ba = row_addr + i
            if ba >= len(data):
                break
            block_base = ba & ~(bs - 1)
            if block_base in hi_blocks:
                return True
            if data[ba] != 0:
                return True
        return False

    def _render(self):
        if self._memory is None:
            return

        t   = self._hex
        t.configure(state=tk.NORMAL)
        t.delete("1.0", tk.END)

        data     = self._memory.data
        bs       = self._block_size
        hi       = self._highlighted_blocks()
        end_mode = self._range_var.get()

        # Determine the address range to iterate
        if end_mode == "Smart":
            # Find last non-zero or highlighted byte, show that + one extra row
            last_interesting = 0
            for a in hi:
                last_interesting = max(last_interesting, a + BPR - 1)
            for idx in range(len(data) - 1, -1, -1):
                if data[idx] != 0:
                    last_interesting = max(last_interesting, idx)
                    break
            end_byte = min(last_interesting + BPR, len(data) - 1)
        else:
            end_byte = min(self._end_addr(), len(data) - 1)

        # Align range to row boundaries
        first_row = 0
        last_row  = ((end_byte // BPR) + 1) * BPR

        scroll_line = None     # line number (1-based) to auto-scroll to
        current_line = 1
        skipped = 0            # count of consecutive boring rows

        for row_addr in range(first_row, last_row, BPR):
            interesting = (end_mode != "Smart" or
                           self._row_is_interesting(row_addr, hi))

            if not interesting:
                skipped += 1
                continue

            # Flush any accumulated skip
            if skipped > 0:
                t.insert(tk.END,
                         f"   … {skipped} row{'s' if skipped != 1 else ''}"
                         f" of zero bytes (0x{row_addr - skipped*BPR:04X}"
                         f"–0x{row_addr - 1:04X})\n",
                         "ellipsis")
                current_line += 1
                skipped = 0

            # Address label
            t.insert(tk.END, f"0x{row_addr:04X}: ", "addr")

            # Hex bytes, grouped by block_size with an extra space between groups
            for i in range(BPR):
                ba         = row_addr + i
                byte       = data[ba] if ba < len(data) else 0
                block_base = ba & ~(bs - 1)
                tag        = self._byte_tag(byte, block_base)

                t.insert(tk.END, f"{byte:02X}", tag)

                if i < BPR - 1:
                    t.insert(tk.END, " ", "zero")
                    if (i + 1) % bs == 0:
                        t.insert(tk.END, " ", "zero")   # extra gap between blocks

            # ASCII column
            t.insert(tk.END, "  |", "addr")
            for i in range(BPR):
                ba         = row_addr + i
                byte       = data[ba] if ba < len(data) else 0
                block_base = ba & ~(bs - 1)
                ch         = chr(byte) if 32 <= byte < 127 else "·"
                t.insert(tk.END, ch, self._byte_tag(byte, block_base))
            t.insert(tk.END, "|\n", "addr")

            # Remember line for auto-scroll
            if self._active is not None:
                row_end = row_addr + BPR - 1
                if row_addr <= self._active <= row_end:
                    scroll_line = f"{current_line}.0"
            elif self._alloc is not None and scroll_line is None:
                row_end = row_addr + BPR - 1
                if row_addr <= self._alloc <= row_end:
                    scroll_line = f"{current_line}.0"

            current_line += 1

        # Flush trailing skip
        if skipped > 0 and end_mode == "Smart":
            t.insert(tk.END,
                     f"   … {skipped} row{'s' if skipped != 1 else ''} of zero bytes\n",
                     "ellipsis")

        t.configure(state=tk.DISABLED)

        # Auto-scroll
        if self._auto_scroll.get() and scroll_line:
            t.see(scroll_line)

    def _byte_tag(self, byte, block_base):
        if   block_base == self._active: return "active"
        elif block_base == self._alloc:  return "alloc"
        elif block_base == self._wb:     return "wb"
        elif byte != 0:                  return "nonzero"
        else:                            return "zero"
