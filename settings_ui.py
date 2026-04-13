"""
Settings dialog (PySide6 QDialog).
Opens non-blocking on the main thread — no separate thread needed.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QObject,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

# Colour palette (matches HUD)
_BG = "#1e1e2e"
_FG = "#cdd6f4"
_ENTRY_BG = "#313244"
_ACCENT = "#89b4fa"
_BTN_BG = "#45475a"
_BTN_HOVER = "#585b70"
_HEADER_BG = "#181825"

_QSS = f"""
QDialog, QWidget {{
    background: {_BG};
    color: {_FG};
}}
QLabel {{
    color: {_FG};
    background: transparent;
}}
QLabel#section_title {{
    color: {_ACCENT};
    font-weight: bold;
    font-size: 11pt;
    padding-top: 12px;
}}
QLineEdit, QPlainTextEdit {{
    background: {_ENTRY_BG};
    color: {_FG};
    border: none;
    border-radius: 3px;
    padding: 4px 6px;
    font-family: monospace;
    font-size: 11pt;
}}
QRadioButton {{
    color: {_FG};
    font-size: 11pt;
    spacing: 6px;
}}
QRadioButton::indicator {{
    width: 14px;
    height: 14px;
}}
QSlider::groove:horizontal {{
    background: {_ENTRY_BG};
    height: 6px;
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background: {_ACCENT};
    width: 14px;
    height: 14px;
    margin: -4px 0;
    border-radius: 7px;
}}
QPushButton {{
    background: {_BTN_BG};
    color: {_FG};
    border: none;
    border-radius: 3px;
    padding: 5px 14px;
    font-size: 11pt;
}}
QPushButton:hover {{
    background: {_BTN_HOVER};
}}
QPushButton#save_btn {{
    background: {_ACCENT};
    color: #1e1e2e;
    font-weight: bold;
    font-size: 12pt;
    padding: 8px 24px;
}}
QPushButton#save_btn:hover {{
    background: #74c7ec;
}}
QScrollArea {{
    border: none;
}}
"""


class SettingsWindow(QObject):
    """Wraps a lazily-created QDialog. Call open() from the main thread."""

    def __init__(self, settings):
        super().__init__()
        self._settings = settings
        self._dialog: _SettingsDialog | None = None

    def open(self):
        """Open the settings dialog. If already open, bring it to the front."""
        if self._dialog and self._dialog.isVisible():
            self._dialog.raise_()
            self._dialog.activateWindow()
            return
        self._dialog = _SettingsDialog(self._settings)
        self._dialog.show()


class _SettingsDialog(QDialog):
    def __init__(self, settings):
        super().__init__(None, Qt.Window)
        self._settings = settings
        self.setWindowTitle("DictaThesis — Settings")
        self.setStyleSheet(_QSS)
        self.setMinimumWidth(520)
        self.resize(560, 620)
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Header
        header_label = QLabel("DictaThesis  ·  Settings")
        header_label.setAlignment(Qt.AlignCenter)
        header_label.setStyleSheet(
            f"background:{_HEADER_BG};color:{_ACCENT};"
            "font-size:15pt;font-weight:bold;padding:14px;"
        )
        outer.addWidget(header_label)

        # Scrollable content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(18, 0, 18, 18)
        layout.setSpacing(4)
        scroll.setWidget(content)
        outer.addWidget(scroll)

        # --- API Key ---
        self._add_section(layout, "Mistral API Key")
        api_row = QHBoxLayout()
        self._api_key_edit = QLineEdit(self._settings.get("api_key") or "")
        self._api_key_edit.setEchoMode(QLineEdit.Password)
        self._api_key_edit.setPlaceholderText("sk-...")
        show_btn = QPushButton("Show")
        show_btn.setFixedWidth(60)
        show_btn.clicked.connect(self._toggle_api_visibility)
        api_row.addWidget(self._api_key_edit)
        api_row.addWidget(show_btn)
        layout.addLayout(api_row)

        # --- Language ---
        self._add_section(layout, "Language")
        lang_row = QHBoxLayout()
        lang = self._settings.get("language") or "fr"
        self._lang_buttons: dict[str, QRadioButton] = {}
        for val, lbl in [("fr", "Français"), ("en", "English"), ("auto", "Auto-detect")]:
            rb = QRadioButton(lbl)
            rb.setChecked(lang == val)
            lang_row.addWidget(rb)
            self._lang_buttons[val] = rb
        lang_row.addStretch()
        layout.addLayout(lang_row)

        # --- Shortcut Key ---
        self._add_section(layout, "Global Shortcut Key")
        shortcut_row = QHBoxLayout()
        self._shortcut_edit = QLineEdit(self._settings.get("shortcut_key") or "f9")
        self._shortcut_edit.setFixedWidth(160)
        hint = QLabel("(e.g. f9, f8, scroll_lock)")
        hint.setStyleSheet("color:#888899;font-size:10pt;")
        shortcut_row.addWidget(self._shortcut_edit)
        shortcut_row.addWidget(hint)
        shortcut_row.addStretch()
        layout.addLayout(shortcut_row)

        # --- VAD Silence Duration ---
        self._add_section(layout, "Silence Duration Before Chunk (seconds)")
        vad_row = QHBoxLayout()
        vad_val = self._settings.get("vad_silence_duration") or 1.5
        self._vad_slider = QSlider(Qt.Horizontal)
        self._vad_slider.setRange(5, 40)   # ×0.1 → 0.5–4.0 s
        self._vad_slider.setSingleStep(1)
        self._vad_slider.setValue(int(round(vad_val * 10)))
        self._vad_slider.setFixedWidth(300)
        self._vad_label = QLabel(f"{vad_val:.1f}")
        self._vad_label.setStyleSheet(f"color:{_ACCENT};font-weight:bold;font-size:11pt;")
        self._vad_label.setFixedWidth(36)
        self._vad_slider.valueChanged.connect(
            lambda v: self._vad_label.setText(f"{v / 10:.1f}")
        )
        vad_row.addWidget(self._vad_slider)
        vad_row.addWidget(self._vad_label)
        vad_row.addStretch()
        layout.addLayout(vad_row)

        # --- Custom Vocabulary ---
        self._add_section(layout, "Custom Vocabulary  (one term per line)")
        self._vocab_edit = QPlainTextEdit()
        self._vocab_edit.setFixedHeight(110)
        self._vocab_edit.setPlainText(self._settings.get_vocabulary_text())
        layout.addWidget(self._vocab_edit)

        # --- Bibliography ---
        self._add_section(layout, "Bibliography  (paste BibTeX or reference list)")
        bib_btn_row = QHBoxLayout()
        load_bib_btn = QPushButton("📂  Load .bib file")
        load_bib_btn.clicked.connect(self._load_bib)
        bib_btn_row.addWidget(load_bib_btn)
        bib_btn_row.addStretch()
        layout.addLayout(bib_btn_row)
        self._biblio_edit = QPlainTextEdit()
        self._biblio_edit.setFixedHeight(130)
        self._biblio_edit.setPlainText(self._settings.get("bibliography") or "")
        layout.addWidget(self._biblio_edit)

        # --- Save button ---
        layout.addSpacing(8)
        save_btn = QPushButton("  Save Settings  ")
        save_btn.setObjectName("save_btn")
        save_btn.clicked.connect(self._save)
        layout.addWidget(save_btn, alignment=Qt.AlignHCenter)
        layout.addSpacing(10)

    def _add_section(self, layout: QVBoxLayout, title: str):
        lbl = QLabel(title)
        lbl.setObjectName("section_title")
        layout.addWidget(lbl)
        sep = QWidget()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{_ENTRY_BG};")
        layout.addWidget(sep)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _toggle_api_visibility(self):
        if self._api_key_edit.echoMode() == QLineEdit.Password:
            self._api_key_edit.setEchoMode(QLineEdit.Normal)
        else:
            self._api_key_edit.setEchoMode(QLineEdit.Password)

    def _load_bib(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load BibTeX file", "", "BibTeX files (*.bib);;All files (*)"
        )
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                self._biblio_edit.setPlainText(f.read())
        except OSError as e:
            QMessageBox.critical(self, "Error", f"Could not read file:\n{e}")

    def _save(self):
        lang = next(
            (code for code, rb in self._lang_buttons.items() if rb.isChecked()), "fr"
        )
        updates = {
            "api_key": self._api_key_edit.text().strip(),
            "language": lang,
            "shortcut_key": self._shortcut_edit.text().strip().lower(),
            "vad_silence_duration": round(self._vad_slider.value() / 10, 1),
            "bibliography": self._biblio_edit.toPlainText().strip(),
        }
        self._settings.update(updates)
        self._settings.set_vocabulary_from_text(self._vocab_edit.toPlainText())
        QMessageBox.information(
            self,
            "Saved",
            "Settings saved.\nRestart DictaThesis for shortcut changes to take effect.",
        )
