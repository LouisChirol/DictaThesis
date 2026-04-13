"""
System tray icon and menu (pystray + Pillow).

States
------
  idle       — blue circle   — ready, not recording
  recording  — red circle    — capturing audio
  processing — orange circle — chunks in flight (still recording or finishing up)

The menu updates dynamically based on recording state.
"""

from __future__ import annotations

from collections.abc import Callable

import pystray
from PIL import Image, ImageDraw

# ---------------------------------------------------------------------------
# Icon generation
# ---------------------------------------------------------------------------


def _make_icon(fill_rgb: tuple[int, int, int], dot_rgb: tuple | None = None) -> Image.Image:
    """64×64 RGBA circle with an optional small indicator dot."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([3, 3, 61, 61], fill=(*fill_rgb, 230))
    # Inner mic silhouette (white rectangle + arc)
    draw.rectangle([27, 18, 37, 42], fill=(255, 255, 255, 210), outline=None)
    draw.arc([21, 13, 43, 33], start=0, end=180, fill=(255, 255, 255, 210), width=3)
    # Stand
    draw.line([(32, 42), (32, 50)], fill=(255, 255, 255, 180), width=2)
    draw.arc([24, 46, 40, 54], start=180, end=0, fill=(255, 255, 255, 180), width=2)
    if dot_rgb:
        draw.ellipse([44, 44, 60, 60], fill=(*dot_rgb, 255))
    return img


ICONS: dict[str, Image.Image] = {
    "idle": _make_icon((55, 110, 200)),
    "recording": _make_icon((200, 45, 45), dot_rgb=(255, 80, 80)),
    "processing": _make_icon((55, 110, 200), dot_rgb=(255, 165, 0)),
}


# ---------------------------------------------------------------------------
# TrayIcon
# ---------------------------------------------------------------------------


class TrayIcon:
    def __init__(
        self,
        on_toggle: Callable[[], None],
        on_settings: Callable[[], None],
        on_quit: Callable[[], None],
        settings,
    ):
        self._on_toggle = on_toggle
        self._on_settings = on_settings
        self._on_quit = on_quit
        self._settings = settings
        self._recording = False
        self._icon: pystray.Icon | None = None

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self):
        """Build and run the tray icon.  Blocks the calling thread."""
        self._icon = pystray.Icon(
            name="DictaThesis",
            icon=ICONS["idle"],
            title="DictaThesis — Ready (F9 to start)",
            menu=self._build_menu(),
        )
        self._icon.run()

    def set_recording(self, recording: bool):
        self._recording = recording
        state = "recording" if recording else "idle"
        self._update_icon(
            state,
            "🔴  Recording…  (F9 or HUD stop button)"
            if recording
            else "DictaThesis — Ready (F9 to start)",
        )

    def set_processing(self):
        self._update_icon("processing", "⏳  Processing…")

    def set_idle(self):
        self._recording = False
        self._update_icon("idle", "DictaThesis — Ready (F9 to start)")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _update_icon(self, state: str, tooltip: str):
        if self._icon:
            self._icon.icon = ICONS[state]
            self._icon.title = tooltip
            # Rebuild menu to reflect new state (Start ↔ Stop label)
            self._icon.menu = self._build_menu()

    def _build_menu(self) -> pystray.Menu:
        toggle_label = "⏹  Stop Dictation" if self._recording else "▶  Start Dictation"

        def _set_lang(lang):
            def _inner(icon, item):
                self._settings.set("language", lang)
                self._icon.menu = self._build_menu()

            return _inner

        def _set_mode(mode):
            def _inner(icon, item):
                self._settings.set("mode", mode)
                self._icon.menu = self._build_menu()

            return _inner

        lang = self._settings.get("language")
        mode = self._settings.get("mode")

        return pystray.Menu(
            pystray.MenuItem(toggle_label, lambda icon, item: self._on_toggle()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                f"Language: {lang.upper()}",
                pystray.Menu(
                    pystray.MenuItem(
                        "Français",
                        _set_lang("fr"),
                        checked=lambda item: self._settings.get("language") == "fr",
                        radio=True,
                    ),
                    pystray.MenuItem(
                        "English",
                        _set_lang("en"),
                        checked=lambda item: self._settings.get("language") == "en",
                        radio=True,
                    ),
                    pystray.MenuItem(
                        "Auto-detect",
                        _set_lang("auto"),
                        checked=lambda item: self._settings.get("language") == "auto",
                        radio=True,
                    ),
                ),
            ),
            pystray.MenuItem(
                f"Mode: {mode.capitalize()}",
                pystray.Menu(
                    pystray.MenuItem(
                        "Normal",
                        _set_mode("normal"),
                        checked=lambda item: self._settings.get("mode") == "normal",
                        radio=True,
                    ),
                    pystray.MenuItem(
                        "Equation  (LaTeX math)",
                        _set_mode("equation"),
                        checked=lambda item: self._settings.get("mode") == "equation",
                        radio=True,
                    ),
                ),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("⚙  Settings", lambda icon, item: self._on_settings()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("✕  Quit", lambda icon, item: self._on_quit()),
        )
