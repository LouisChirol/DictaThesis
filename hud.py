"""
Floating always-on-top HUD window (PySide6).

The HUD is the primary UI surface on all platforms. It contains:
  - Status bar with recording state
  - Start / Stop / Settings / Quit buttons
  - Scrollable text area showing draft and final chunks
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

_ACCENT = "#89b4fa"


class HUD(QWidget):
    status_changed = Signal(str)
    chunk_added = Signal(str, str)
    chunk_finalized = Signal(str, str)
    clear_requested = Signal()

    def __init__(
        self,
        on_start: Callable[[], None] | None = None,
        on_stop: Callable[[], None] | None = None,
        on_settings: Callable[[], None] | None = None,
        on_quit: Callable[[], None] | None = None,
    ):
        super().__init__(None, Qt.WindowStaysOnTopHint)

        self._on_start = on_start
        self._on_stop = on_stop
        self._on_settings = on_settings
        self._on_quit = on_quit
        self._chunks: dict[str, dict] = {}

        self.setWindowTitle("DictaThesis")
        self.setWindowOpacity(0.95)
        self.setGeometry(60, 60, 480, 260)
        self.setMinimumSize(350, 180)

        self._build_ui()
        self._connect_signals()

    def _build_ui(self):
        self.setStyleSheet(f"""
            QWidget#hud {{
                background: #1e1e2e;
            }}
            QLabel#status {{
                color: {_ACCENT};
                font-weight: bold;
                padding: 2px;
            }}
        """)
        self.setObjectName("hud")

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # ---- Button bar ----
        bar = QHBoxLayout()
        bar.setSpacing(6)

        self._start_btn = self._make_btn("Start", "#06d6a0", "#1e1e2e")
        self._start_btn.clicked.connect(lambda: self._on_start and self._on_start())
        bar.addWidget(self._start_btn)

        self._stop_btn = self._make_btn("Stop", "#e63946", "#ffffff")
        self._stop_btn.clicked.connect(lambda: self._on_stop and self._on_stop())
        self._stop_btn.setEnabled(False)
        bar.addWidget(self._stop_btn)

        bar.addStretch()

        settings_btn = self._make_btn("Settings", "#45475a", "#cdd6f4")
        settings_btn.clicked.connect(lambda: self._on_settings and self._on_settings())
        bar.addWidget(settings_btn)

        quit_btn = self._make_btn("Quit", "#45475a", "#cdd6f4")
        quit_btn.clicked.connect(lambda: self._on_quit and self._on_quit())
        bar.addWidget(quit_btn)

        root.addLayout(bar)

        # ---- Status label ----
        self._status_label = QLabel("Ready")
        self._status_label.setObjectName("status")
        self._status_label.setFont(QFont("sans-serif", 10))
        root.addWidget(self._status_label)

        # ---- Text area ----
        self._text = QTextEdit()
        self._text.setReadOnly(True)
        self._text.setFont(QFont("sans-serif", 11))
        self._text.setStyleSheet("""
            QTextEdit {
                background: #11111b;
                color: #cdd6f4;
                border: 1px solid #313244;
                border-radius: 6px;
                padding: 8px;
            }
        """)
        root.addWidget(self._text)

    def _make_btn(self, text: str, bg: str, fg: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFont(QFont("sans-serif", 9, QFont.Bold))
        btn.setStyleSheet(f"""
            QPushButton {{
                background: {bg};
                color: {fg};
                border: none;
                padding: 5px 14px;
                border-radius: 4px;
            }}
            QPushButton:hover {{
                opacity: 0.85;
            }}
            QPushButton:disabled {{
                background: #313244;
                color: #585b70;
            }}
        """)
        return btn

    def _connect_signals(self):
        self.status_changed.connect(self._set_status)
        self.chunk_added.connect(self._add_chunk)
        self.chunk_finalized.connect(self._finalize_chunk)
        self.clear_requested.connect(self._clear)

    # ------------------------------------------------------------------
    # Thread-safe public API (emit signals, safe from any thread)
    # ------------------------------------------------------------------

    def set_status(self, text: str):
        self.status_changed.emit(text)

    def add_chunk(self, chunk_id: str, draft_text: str):
        self.chunk_added.emit(chunk_id, draft_text)

    def finalize_chunk(self, chunk_id: str, final_text: str):
        self.chunk_finalized.emit(chunk_id, final_text)

    def clear(self):
        self.clear_requested.emit()

    def set_recording_ui(self, recording: bool):
        """Update button states based on recording status."""
        self._start_btn.setEnabled(not recording)
        self._stop_btn.setEnabled(recording)

    def wait_ready(self, timeout: float = 5.0):
        pass

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _set_status(self, text: str):
        self._status_label.setText(text)

    def _add_chunk(self, chunk_id: str, draft_text: str):
        self._chunks[chunk_id] = {"text": draft_text, "state": "draft"}
        self._rebuild_html()

    def _finalize_chunk(self, chunk_id: str, final_text: str):
        if chunk_id not in self._chunks:
            return
        self._chunks[chunk_id] = {"text": final_text, "state": "final"}
        self._rebuild_html()
        QTimer.singleShot(3000, lambda cid=chunk_id: self._settle_chunk(cid))

    def _settle_chunk(self, chunk_id: str):
        if chunk_id in self._chunks:
            self._chunks[chunk_id]["state"] = "settled"
            self._rebuild_html()

    def _clear(self):
        self._chunks.clear()
        self._text.setHtml("")

    def _rebuild_html(self):
        lines = []
        for cid, chunk in self._chunks.items():
            text = chunk["text"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            s = chunk["state"]
            if s == "draft":
                fg, bg = "#ffd166", "#3a2a00"
            elif s == "final":
                fg, bg = "#06d6a0", "#003322"
            else:
                fg, bg = "#666677", "transparent"
            lines.append(
                f'<p style="margin:2px 0;padding:3px 6px;background:{bg};color:{fg};'
                f'border-radius:3px;">'
                f'<span style="color:#585b70;font-size:9pt;">[{cid}]</span> {text}</p>'
            )
        self._text.setHtml("".join(lines))
        sb = self._text.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ------------------------------------------------------------------
    # Close event
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        if self._on_quit:
            self._on_quit()
        event.accept()
