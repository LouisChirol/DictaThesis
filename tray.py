"""
System tray icon and menu (PySide6 QSystemTrayIcon).

States
------
  idle       — blue circle   — ready, not recording
  recording  — red circle    — capturing audio
  processing — orange circle — chunks in flight

Icons are drawn with QPainter (no Pillow dependency).
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

# ---------------------------------------------------------------------------
# Icon generation
# ---------------------------------------------------------------------------


def _make_icon(
    fill_rgb: tuple[int, int, int],
    dot_rgb: tuple[int, int, int] | None = None,
) -> QIcon:
    """64×64 icon: coloured circle with microphone silhouette + optional dot."""
    px = QPixmap(64, 64)
    px.fill(Qt.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing)

    # Main circle
    p.setBrush(QColor(*fill_rgb, 230))
    p.setPen(Qt.NoPen)
    p.drawEllipse(3, 3, 58, 58)

    # Microphone body (white rectangle)
    p.setBrush(QColor(255, 255, 255, 210))
    p.drawRoundedRect(27, 18, 10, 24, 5, 5)

    # Microphone arc
    from PySide6.QtGui import QPen
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(QColor(255, 255, 255, 210), 3))
    p.drawArc(18, 18, 28, 28, 0 * 16, -180 * 16)  # bottom half arc

    # Stand line
    p.setPen(QPen(QColor(255, 255, 255, 180), 2))
    p.drawLine(32, 46, 32, 52)

    # Stand base arc
    p.drawArc(24, 48, 16, 8, 0 * 16, -180 * 16)

    # Optional indicator dot
    if dot_rgb:
        p.setBrush(QColor(*dot_rgb, 255))
        p.setPen(Qt.NoPen)
        p.drawEllipse(44, 44, 16, 16)

    p.end()
    return QIcon(px)


_ICONS: dict[str, QIcon] = {}


def _get_icons() -> dict[str, QIcon]:
    """Lazy-initialise icons (QPixmap requires QApplication to exist first)."""
    if not _ICONS:
        _ICONS["idle"] = _make_icon((55, 110, 200))
        _ICONS["recording"] = _make_icon((200, 45, 45), dot_rgb=(255, 80, 80))
        _ICONS["processing"] = _make_icon((55, 110, 200), dot_rgb=(255, 165, 0))
    return _ICONS


# ---------------------------------------------------------------------------
# TrayIcon
# ---------------------------------------------------------------------------


class TrayIcon(QObject):
    def __init__(
        self,
        on_toggle: Callable[[], None],
        on_settings: Callable[[], None],
        on_quit: Callable[[], None],
        settings,
    ):
        super().__init__()
        self._on_toggle = on_toggle
        self._on_settings = on_settings
        self._on_quit = on_quit
        self._settings = settings
        self._recording = False
        self._icon: QSystemTrayIcon | None = None

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self):
        """Initialise and show the tray icon. Non-blocking."""
        icons = _get_icons()
        self._icon = QSystemTrayIcon(icons["idle"])
        self._icon.setToolTip("DictaThesis — Ready (F9 to start)")
        self._icon.setContextMenu(self._build_menu())
        self._icon.activated.connect(self._on_activated)
        self._icon.show()

    def set_recording(self, recording: bool):
        self._recording = recording
        icons = _get_icons()
        if recording:
            self._icon.setIcon(icons["recording"])
            self._icon.setToolTip("🔴  Recording…  (F9 or HUD stop button)")
        else:
            self._icon.setIcon(icons["idle"])
            self._icon.setToolTip("DictaThesis — Ready (F9 to start)")
        self._icon.setContextMenu(self._build_menu())

    def set_processing(self):
        icons = _get_icons()
        self._icon.setIcon(icons["processing"])
        self._icon.setToolTip("⏳  Processing…")
        self._icon.setContextMenu(self._build_menu())

    def set_idle(self):
        self._recording = False
        icons = _get_icons()
        self._icon.setIcon(icons["idle"])
        self._icon.setToolTip("DictaThesis — Ready (F9 to start)")
        self._icon.setContextMenu(self._build_menu())

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason):
        if reason == QSystemTrayIcon.Trigger:  # left-click
            self._on_toggle()

    def _build_menu(self) -> QMenu:
        menu = QMenu()
        toggle_label = "⏹  Stop Dictation" if self._recording else "▶  Start Dictation"
        menu.addAction(toggle_label, self._on_toggle)
        menu.addSeparator()

        # Language submenu
        lang = self._settings.get("language")
        lang_menu = menu.addMenu(f"Language: {lang.upper()}")
        for code, label in [("fr", "Français"), ("en", "English"), ("auto", "Auto-detect")]:
            action = lang_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(lang == code)
            action.triggered.connect(lambda checked, c=code: self._set_lang(c))

        # Mode submenu
        mode = self._settings.get("mode")
        mode_menu = menu.addMenu(f"Mode: {mode.capitalize()}")
        for code, label in [("normal", "Normal"), ("equation", "Equation  (LaTeX math)")]:
            action = mode_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(mode == code)
            action.triggered.connect(lambda checked, m=code: self._set_mode(m))

        menu.addSeparator()
        menu.addAction("⚙  Settings", self._on_settings)
        menu.addSeparator()
        menu.addAction("✕  Quit", self._on_quit)
        return menu

    def _set_lang(self, lang: str):
        self._settings.set("language", lang)
        self._icon.setContextMenu(self._build_menu())

    def _set_mode(self, mode: str):
        self._settings.set("mode", mode)
        self._icon.setContextMenu(self._build_menu())
