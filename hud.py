"""
Floating always-on-top HUD window (PySide6).

Shows dictation pipeline state in real time:
  - Each chunk appears as a row: draft (yellow) → final (green) → settled (grey)
  - A status bar shows current recording state
  - A Stop button lets the user end the session with one click

All public methods are thread-safe via Qt signals.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

# Colour palette
_BG = "#1a1a2e"
_BAR_BG = "#16213e"
_TEXT_FG = "#e0e0e0"
_DRAFT_FG = "#ffd166"
_DRAFT_BG = "#3a2a00"
_FINAL_FG = "#06d6a0"
_FINAL_BG = "#003322"
_SETTLED_FG = "#666677"
_STOP_BG = "#e63946"
_STOP_HOVER = "#c1121f"

_QSS = f"""
QWidget#hud_root {{
    background: {_BG};
}}
QFrame#header {{
    background: {_BAR_BG};
    border: none;
}}
QLabel#status_label {{
    color: {_TEXT_FG};
    font-family: Helvetica, Arial, sans-serif;
    font-size: 11pt;
    font-weight: bold;
    padding-left: 10px;
    background: transparent;
}}
QPushButton#stop_btn {{
    background: {_STOP_BG};
    color: white;
    font-family: Helvetica, Arial, sans-serif;
    font-size: 10pt;
    font-weight: bold;
    border: none;
    padding: 3px 12px;
    border-radius: 3px;
}}
QPushButton#stop_btn:hover {{
    background: {_STOP_HOVER};
}}
QTextEdit#chunk_view {{
    background: #0f0f1a;
    color: {_TEXT_FG};
    font-family: Helvetica, Arial, sans-serif;
    font-size: 12pt;
    border: none;
    padding: 6px 8px;
}}
QScrollBar:vertical {{
    background: {_BG};
    width: 8px;
    border: none;
}}
QScrollBar::handle:vertical {{
    background: #313244;
    border-radius: 4px;
    min-height: 20px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
"""


class HUD(QWidget):
    # Signals — emitting from any thread is safe; slots run on main thread
    status_changed = Signal(str)
    chunk_added = Signal(str, str)      # chunk_id, draft_text
    chunk_finalized = Signal(str, str)  # chunk_id, final_text
    clear_requested = Signal()

    def __init__(self, on_stop: Callable[[], None] | None = None):
        super().__init__(
            None,
            Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool,
        )
        self._on_stop = on_stop
        self._drag_pos = QPoint()
        self._chunks: dict[str, dict] = {}  # chunk_id → {text, state}

        self.setObjectName("hud_root")
        self.setWindowOpacity(0.93)
        self.setGeometry(60, 60, 440, 230)
        self.setMinimumSize(300, 150)
        self.setStyleSheet(_QSS)

        self._build_ui()
        self._connect_signals()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # --- Header bar ---
        header = QFrame(self)
        header.setObjectName("header")
        header.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 4, 8, 4)
        header_layout.setSpacing(0)

        self._status_label = QLabel("DictaThesis  ·  F9 to start", header)
        self._status_label.setObjectName("status_label")
        self._status_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        header_layout.addWidget(self._status_label)

        stop_btn = QPushButton("⏹  Stop", header)
        stop_btn.setObjectName("stop_btn")
        stop_btn.setCursor(Qt.PointingHandCursor)
        stop_btn.clicked.connect(self._handle_stop)
        header_layout.addWidget(stop_btn)

        root_layout.addWidget(header)

        # --- Text area ---
        self._text = QTextEdit(self)
        self._text.setObjectName("chunk_view")
        self._text.setReadOnly(True)
        self._text.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._text.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        root_layout.addWidget(self._text)

        # Enable drag on header and status label
        header.mousePressEvent = self._drag_start
        header.mouseMoveEvent = self._drag_move
        self._status_label.mousePressEvent = self._drag_start
        self._status_label.mouseMoveEvent = self._drag_move

    def _connect_signals(self):
        self.status_changed.connect(self._set_status)
        self.chunk_added.connect(self._add_chunk)
        self.chunk_finalized.connect(self._finalize_chunk)
        self.clear_requested.connect(self._clear)

    # ------------------------------------------------------------------
    # Thread-safe public API
    # ------------------------------------------------------------------

    def set_status(self, text: str):
        self.status_changed.emit(text)

    def add_chunk(self, chunk_id: str, draft_text: str):
        self.chunk_added.emit(chunk_id, draft_text)

    def finalize_chunk(self, chunk_id: str, final_text: str):
        self.chunk_finalized.emit(chunk_id, final_text)

    def clear(self):
        self.clear_requested.emit()

    def wait_ready(self, timeout: float = 5.0):
        """No-op — QWidget is ready as soon as show() returns."""

    # ------------------------------------------------------------------
    # Slots (always run on main thread)
    # ------------------------------------------------------------------

    def _set_status(self, text: str):
        self._status_label.setText(text)

    def _add_chunk(self, chunk_id: str, draft_text: str):
        self._chunks[chunk_id] = {"text": draft_text, "state": "draft"}
        self._rebuild_html()
        self._scroll_to_bottom()

    def _finalize_chunk(self, chunk_id: str, final_text: str):
        if chunk_id not in self._chunks:
            return
        self._chunks[chunk_id] = {"text": final_text, "state": "final"}
        self._rebuild_html()
        self._scroll_to_bottom()
        QTimer.singleShot(3000, lambda cid=chunk_id: self._settle_chunk(cid))

    def _settle_chunk(self, chunk_id: str):
        if chunk_id not in self._chunks:
            return
        self._chunks[chunk_id]["state"] = "settled"
        self._rebuild_html()

    def _clear(self):
        self._chunks.clear()
        self._text.setHtml("")

    # ------------------------------------------------------------------
    # HTML rendering
    # ------------------------------------------------------------------

    def _rebuild_html(self):
        parts = ["<html><body style='margin:0;padding:0;'>"]
        for cid, chunk in self._chunks.items():
            state = chunk["state"]
            text = (
                chunk["text"]
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )
            if state == "draft":
                fg, bg = _DRAFT_FG, _DRAFT_BG
            elif state == "final":
                fg, bg = _FINAL_FG, _FINAL_BG
            else:
                fg, bg = _SETTLED_FG, _BG
            parts.append(
                f'<p style="margin:2px 0;padding:2px 4px;background:{bg};color:{fg};">'
                f'<span style="color:#555566;font-size:9pt;">[{cid}]</span> {text}'
                f"</p>"
            )
        parts.append("</body></html>")
        self._text.setHtml("".join(parts))

    def _scroll_to_bottom(self):
        sb = self._text.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ------------------------------------------------------------------
    # Stop button
    # ------------------------------------------------------------------

    def _handle_stop(self):
        if self._on_stop:
            self._on_stop()

    # ------------------------------------------------------------------
    # Window drag
    # ------------------------------------------------------------------

    def _drag_start(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def _drag_move(self, event):
        if event.buttons() & Qt.LeftButton and not self._drag_pos.isNull():
            self.move(event.globalPosition().toPoint() - self._drag_pos)
