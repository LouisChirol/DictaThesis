"""
Settings window — opens in its own thread.
Uses tkinter directly (no customtkinter dependency) for portability.
"""
from __future__ import annotations
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import Optional


class SettingsWindow:
    BG = "#1e1e2e"
    FG = "#cdd6f4"
    ENTRY_BG = "#313244"
    ACCENT = "#89b4fa"
    BTN_BG = "#45475a"
    BTN_HOVER = "#585b70"
    HEADER_BG = "#181825"

    def __init__(self, settings):
        self._settings = settings
        self._root: Optional[tk.Tk] = None
        self._lock = threading.Lock()

    def open(self):
        """Open the settings window. If already open, bring it to front."""
        with self._lock:
            if self._root and self._root.winfo_exists():
                self._root.lift()
                self._root.focus_force()
                return
        t = threading.Thread(target=self._run, daemon=True)
        t.start()

    def _run(self):
        root = tk.Tk()
        self._root = root
        root.title("DictaThesis — Settings")
        root.configure(bg=self.BG)
        root.geometry("560x620")
        root.resizable(False, True)
        root.minsize(480, 500)

        self._build_ui(root)
        root.mainloop()

    def _build_ui(self, root: tk.Tk):
        s = ttk.Style()
        s.theme_use("default")

        # Scrollable canvas
        canvas = tk.Canvas(root, bg=self.BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(root, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        frame = tk.Frame(canvas, bg=self.BG)
        frame_id = canvas.create_window((0, 0), window=frame, anchor="nw")

        def _resize(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfig(frame_id, width=event.width)

        frame.bind("<Configure>", _resize)
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(frame_id, width=e.width))

        pad = dict(padx=18, pady=6)

        # --- Header ---
        hdr = tk.Frame(frame, bg=self.HEADER_BG)
        hdr.pack(fill="x")
        tk.Label(
            hdr, text="DictaThesis  ·  Settings",
            bg=self.HEADER_BG, fg=self.ACCENT,
            font=("Helvetica", 15, "bold"), pady=14,
        ).pack()

        # --- API Key ---
        self._section(frame, "Mistral API Key")
        self._api_key_var = tk.StringVar(value=self._settings.get("api_key"))
        api_row = tk.Frame(frame, bg=self.BG)
        api_row.pack(fill="x", **pad)
        api_entry = tk.Entry(
            api_row, textvariable=self._api_key_var, show="•",
            bg=self.ENTRY_BG, fg=self.FG, insertbackground=self.FG,
            relief="flat", font=("Courier", 11), width=38,
        )
        api_entry.pack(side="left", ipady=4, padx=(0, 8))
        tk.Button(
            api_row, text="Show",
            bg=self.BTN_BG, fg=self.FG, relief="flat",
            command=lambda: api_entry.config(
                show="" if api_entry.cget("show") == "•" else "•"
            ),
        ).pack(side="left")

        # --- Language ---
        self._section(frame, "Language")
        self._lang_var = tk.StringVar(value=self._settings.get("language"))
        lang_frame = tk.Frame(frame, bg=self.BG)
        lang_frame.pack(fill="x", **pad)
        for val, lbl in [("fr", "Français"), ("en", "English"), ("auto", "Auto-detect")]:
            tk.Radiobutton(
                lang_frame, text=lbl, variable=self._lang_var, value=val,
                bg=self.BG, fg=self.FG, selectcolor=self.ENTRY_BG,
                activebackground=self.BG, activeforeground=self.ACCENT,
                font=("Helvetica", 11),
            ).pack(side="left", padx=8)

        # --- Shortcut ---
        self._section(frame, "Global Shortcut Key")
        self._shortcut_var = tk.StringVar(value=self._settings.get("shortcut_key"))
        shortcut_frame = tk.Frame(frame, bg=self.BG)
        shortcut_frame.pack(fill="x", **pad)
        tk.Entry(
            shortcut_frame, textvariable=self._shortcut_var,
            bg=self.ENTRY_BG, fg=self.FG, insertbackground=self.FG,
            relief="flat", font=("Courier", 11), width=16,
        ).pack(side="left", ipady=4, padx=(0, 8))
        tk.Label(
            shortcut_frame,
            text="(e.g. f9, f8, scroll_lock)",
            bg=self.BG, fg="#888899", font=("Helvetica", 10),
        ).pack(side="left")

        # --- VAD Silence Duration ---
        self._section(frame, "Silence Duration Before Chunk (seconds)")
        self._vad_var = tk.DoubleVar(value=self._settings.get("vad_silence_duration"))
        vad_frame = tk.Frame(frame, bg=self.BG)
        vad_frame.pack(fill="x", **pad)
        tk.Scale(
            vad_frame, variable=self._vad_var, from_=0.5, to=4.0, resolution=0.1,
            orient="horizontal", length=300,
            bg=self.BG, fg=self.FG, troughcolor=self.ENTRY_BG,
            highlightthickness=0, activebackground=self.ACCENT,
        ).pack(side="left")
        tk.Label(
            vad_frame, textvariable=self._vad_var,
            bg=self.BG, fg=self.ACCENT, font=("Helvetica", 11, "bold"), width=4,
        ).pack(side="left", padx=8)

        # --- Custom Vocabulary ---
        self._section(frame, "Custom Vocabulary  (one term per line)")
        self._vocab_text = tk.Text(
            frame,
            bg=self.ENTRY_BG, fg=self.FG, insertbackground=self.FG,
            relief="flat", font=("Helvetica", 11),
            height=5, wrap="word",
        )
        self._vocab_text.pack(fill="x", padx=18, pady=(0, 6))
        self._vocab_text.insert("1.0", self._settings.get_vocabulary_text())

        # --- Bibliography ---
        self._section(frame, "Bibliography  (paste BibTeX or reference list)")
        biblio_btn_row = tk.Frame(frame, bg=self.BG)
        biblio_btn_row.pack(fill="x", padx=18, pady=(0, 4))
        tk.Button(
            biblio_btn_row, text="📂  Load .bib file",
            bg=self.BTN_BG, fg=self.FG, relief="flat", padx=8,
            command=self._load_bib,
        ).pack(side="left")
        self._biblio_text = tk.Text(
            frame,
            bg=self.ENTRY_BG, fg=self.FG, insertbackground=self.FG,
            relief="flat", font=("Courier", 10),
            height=6, wrap="none",
        )
        self._biblio_text.pack(fill="x", padx=18, pady=(0, 6))
        self._biblio_text.insert("1.0", self._settings.get("bibliography") or "")

        # --- Save button ---
        tk.Frame(frame, bg=self.BG, height=8).pack()
        tk.Button(
            frame,
            text="  Save Settings  ",
            bg=self.ACCENT, fg="#1e1e2e",
            activebackground="#74c7ec",
            font=("Helvetica", 12, "bold"),
            relief="flat", padx=16, pady=8,
            command=self._save,
        ).pack(pady=(0, 18))

    def _section(self, parent, title: str):
        tk.Label(
            parent, text=title,
            bg=self.BG, fg=self.ACCENT,
            font=("Helvetica", 11, "bold"),
            anchor="w", padx=18, pady=(12, 2),
        ).pack(fill="x")
        tk.Frame(parent, bg="#313244", height=1).pack(fill="x", padx=18)

    def _load_bib(self):
        path = filedialog.askopenfilename(
            filetypes=[("BibTeX files", "*.bib"), ("All files", "*.*")]
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            self._biblio_text.delete("1.0", "end")
            self._biblio_text.insert("1.0", content)
        except OSError as e:
            messagebox.showerror("Error", f"Could not read file:\n{e}")

    def _save(self):
        updates = {
            "api_key": self._api_key_var.get().strip(),
            "language": self._lang_var.get(),
            "shortcut_key": self._shortcut_var.get().strip().lower(),
            "vad_silence_duration": round(self._vad_var.get(), 1),
            "bibliography": self._biblio_text.get("1.0", "end").strip(),
        }
        self._settings.update(updates)
        self._settings.set_vocabulary_from_text(
            self._vocab_text.get("1.0", "end")
        )
        messagebox.showinfo("Saved", "Settings saved.\nRestart DictaThesis for shortcut changes to take effect.")
