"""
Floating always-on-top HUD window (tkinter).

Shows dictation pipeline state in real time:
  - Each chunk appears as a row: draft (yellow) → final (green) → settled (grey)
  - A status bar shows current recording state
  - A Stop button lets the user end the session with one click

Must be run in its own thread. All updates from other threads go via
root.after(0, fn) which is tkinter's thread-safe update mechanism.
"""

from __future__ import annotations

import threading
import tkinter as tk
from collections.abc import Callable


class HUD:
    # Colour palette
    BG = "#1a1a2e"
    BAR_BG = "#16213e"
    TEXT_FG = "#e0e0e0"
    DRAFT_FG = "#ffd166"
    DRAFT_BG = "#3a2a00"
    FINAL_FG = "#06d6a0"
    FINAL_BG = "#003322"
    SETTLED_FG = "#666677"
    STOP_BG = "#e63946"
    STOP_HOVER = "#c1121f"

    def __init__(self, on_stop: Callable[[], None] | None = None):
        self._on_stop = on_stop
        self._root: tk.Tk | None = None
        self._text: tk.Text | None = None
        self._status_var: tk.StringVar | None = None
        self._ready = threading.Event()
        self._chunk_tags: dict[str, tuple[str, str]] = {}  # chunk_id → (start, end)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def run(self):
        """
        Build and run the HUD.  Call this in a dedicated thread.
        Blocks until the window is destroyed.
        """
        root = tk.Tk()
        self._root = root

        root.title("DictaThesis")
        root.configure(bg=self.BG)
        root.attributes("-topmost", True)
        root.attributes("-alpha", 0.93)
        root.geometry("440x230+60+60")
        root.resizable(True, True)
        root.minsize(300, 150)

        # Allow dragging the window by its header bar
        self._drag_x = 0
        self._drag_y = 0

        # --- Top bar ---
        bar = tk.Frame(root, bg=self.BAR_BG, pady=4)
        bar.pack(fill="x")
        bar.bind("<ButtonPress-1>", self._drag_start)
        bar.bind("<B1-Motion>", self._drag_motion)

        self._status_var = tk.StringVar(value="DictaThesis  ·  F9 to start")
        status_lbl = tk.Label(
            bar,
            textvariable=self._status_var,
            bg=self.BAR_BG,
            fg=self.TEXT_FG,
            font=("Helvetica", 11, "bold"),
            anchor="w",
            padx=10,
        )
        status_lbl.pack(side="left", fill="x", expand=True)
        status_lbl.bind("<ButtonPress-1>", self._drag_start)
        status_lbl.bind("<B1-Motion>", self._drag_motion)

        stop_btn = tk.Button(
            bar,
            text="⏹  Stop",
            bg=self.STOP_BG,
            fg="white",
            activebackground=self.STOP_HOVER,
            activeforeground="white",
            font=("Helvetica", 10, "bold"),
            relief="flat",
            padx=12,
            pady=2,
            cursor="hand2",
            command=self._handle_stop,
        )
        stop_btn.pack(side="right", padx=8, pady=2)

        # --- Scrollable text area ---
        text_frame = tk.Frame(root, bg=self.BG)
        text_frame.pack(fill="both", expand=True, padx=6, pady=(4, 6))

        scroll = tk.Scrollbar(text_frame, orient="vertical")
        self._text = tk.Text(
            text_frame,
            bg="#0f0f1a",
            fg=self.TEXT_FG,
            font=("Helvetica", 12),
            wrap="word",
            state="disabled",
            relief="flat",
            yscrollcommand=scroll.set,
            padx=8,
            pady=6,
            cursor="arrow",
            selectbackground="#333355",
        )
        scroll.config(command=self._text.yview)
        scroll.pack(side="right", fill="y")
        self._text.pack(side="left", fill="both", expand=True)

        # Configure text tags
        self._text.tag_configure("draft", foreground=self.DRAFT_FG, background=self.DRAFT_BG)
        self._text.tag_configure("final", foreground=self.FINAL_FG, background=self.FINAL_BG)
        self._text.tag_configure("settled", foreground=self.SETTLED_FG, background=self.BG)
        self._text.tag_configure("label", foreground="#555566")

        self._ready.set()
        root.mainloop()

    def wait_ready(self, timeout: float = 5.0):
        self._ready.wait(timeout)

    # ------------------------------------------------------------------
    # Thread-safe public API (safe to call from any thread)
    # ------------------------------------------------------------------

    def set_status(self, text: str):
        self._schedule(lambda: self._status_var.set(text))  # type: ignore[arg-type]

    def add_chunk(self, chunk_id: str, draft_text: str):
        self._schedule(lambda cid=chunk_id, t=draft_text: self._add_chunk(cid, t))

    def finalize_chunk(self, chunk_id: str, final_text: str):
        self._schedule(lambda cid=chunk_id, t=final_text: self._finalize_chunk(cid, t))

    def clear(self):
        self._schedule(self._clear)

    # ------------------------------------------------------------------
    # Internal (always called from main/tkinter thread via root.after)
    # ------------------------------------------------------------------

    def _schedule(self, fn):
        if self._root:
            self._root.after(0, fn)

    def _add_chunk(self, chunk_id: str, draft_text: str):
        w = self._text
        w.config(state="normal")
        start = w.index("end-1c")
        w.insert("end", f"[{chunk_id}] ", ("label",))
        w.insert("end", draft_text + "\n", ("draft", chunk_id))
        end = w.index("end-1c")
        w.tag_add(chunk_id, start, end)
        w.config(state="disabled")
        w.see("end")
        self._chunk_tags[chunk_id] = (start, end)

    def _finalize_chunk(self, chunk_id: str, final_text: str):
        w = self._text
        if chunk_id not in self._chunk_tags:
            return
        try:
            ranges = w.tag_ranges(chunk_id)
            if not ranges:
                return
            start, end = str(ranges[0]), str(ranges[1])
            w.config(state="normal")
            w.delete(start, end)
            w.insert(start, f"[{chunk_id}] ", ("label",))
            w.insert(f"{start} + {len(chunk_id) + 3}c", final_text + "\n", ("final", chunk_id))
            # Re-tag the whole range
            new_end = w.index(f"{start} lineend +1c")
            w.tag_remove(chunk_id, "1.0", "end")
            w.tag_add(chunk_id, start, new_end)
            w.config(state="disabled")
            w.see("end")
            # Fade to settled after 3 s
            self._root.after(3000, lambda cid=chunk_id: self._settle_chunk(cid))
        except Exception as e:
            print(f"[hud] finalize error: {e}")

    def _settle_chunk(self, chunk_id: str):
        w = self._text
        try:
            ranges = w.tag_ranges(chunk_id)
            if not ranges:
                return
            start, end = str(ranges[0]), str(ranges[1])
            w.config(state="normal")
            w.tag_remove("draft", start, end)
            w.tag_remove("final", start, end)
            w.tag_add("settled", start, end)
            w.config(state="disabled")
        except Exception:
            pass

    def _clear(self):
        w = self._text
        w.config(state="normal")
        w.delete("1.0", "end")
        w.config(state="disabled")
        self._chunk_tags.clear()

    def _handle_stop(self):
        if self._on_stop:
            self._on_stop()

    # ------------------------------------------------------------------
    # Window drag
    # ------------------------------------------------------------------

    def _drag_start(self, event):
        self._drag_x = event.x_root
        self._drag_y = event.y_root

    def _drag_motion(self, event):
        if not self._root:
            return
        dx = event.x_root - self._drag_x
        dy = event.y_root - self._drag_y
        x = self._root.winfo_x() + dx
        y = self._root.winfo_y() + dy
        self._root.geometry(f"+{x}+{y}")
        self._drag_x = event.x_root
        self._drag_y = event.y_root
