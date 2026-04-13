"""
DictaThesis - entry point.

Threading model
---------------
  Main thread    : Qt event loop + asyncio (via qasync)
  pynput thread  : global keyboard listener (optional, disabled on WSL2)
  sounddevice    : audio callback thread (auto-created by sounddevice)
"""

from __future__ import annotations

import asyncio
import os
import platform
import signal
import sys

if platform.system() == "Linux" and "QT_QPA_PLATFORM" not in os.environ:
    os.environ["QT_QPA_PLATFORM"] = "xcb"

import qasync
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from audio import AudioCapture
from hud import HUD
from pipeline import Pipeline
from settings_store import SettingsStore
from settings_ui import SettingsWindow
from tray import TrayIcon


def _is_wsl() -> bool:
    if platform.system() != "Linux":
        return False
    try:
        with open("/proc/version") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


_IS_WSL = _is_wsl()

# pynput crashes X11 on WSL2; only import on platforms where it works
_kb_module = None
_KEY_MAP: dict[str, object] = {}

if not _IS_WSL:
    try:
        from pynput import keyboard as _kb_module
        _KEY_MAP = {
            f"f{i}": getattr(_kb_module.Key, f"f{i}") for i in range(1, 13)
        }
        for name in ("scroll_lock", "pause", "insert", "home", "end", "page_up", "page_down"):
            _KEY_MAP[name] = getattr(_kb_module.Key, name)
    except Exception:
        _kb_module = None


class DictaThesis:
    def __init__(self):
        self.settings = SettingsStore()
        self.loop: asyncio.AbstractEventLoop | None = None
        self._recording = False
        self._listener = None
        self._qapp: QApplication | None = None

    # ------------------------------------------------------------------
    # Dictation control
    # ------------------------------------------------------------------

    def _toggle_dictation(self):
        if self._recording:
            self._stop_dictation()
        else:
            self._start_dictation()

    def _start_dictation(self):
        if self._recording:
            return
        api_key = self.settings.get("api_key")
        if not api_key:
            self.hud.set_status("No API key - open Settings first")
            self.settings_ui.open()
            return

        self.pipeline.start_session()
        try:
            self.audio.start()
        except Exception as e:
            self.pipeline.stop_session()
            self.hud.set_status(f"Audio error: {e}")
            print(f"[main] Audio error: {e}")
            return

        self._recording = True
        self.hud.clear()
        self.hud.set_status("Recording... (press Stop to end)")
        self.hud.set_recording_ui(True)
        self.tray.set_recording(True)

    def _stop_dictation(self):
        if not self._recording:
            return
        self._recording = False
        self.audio.stop()
        self.pipeline.stop_session()
        self.hud.set_status("Finishing... processing remaining chunks")
        self.hud.set_recording_ui(False)
        self.tray.set_processing()

    # ------------------------------------------------------------------
    # Pipeline callbacks
    # ------------------------------------------------------------------

    def _on_draft(self, chunk_id: str, draft_text: str):
        self.hud.add_chunk(chunk_id, draft_text)

    def _on_final(self, chunk_id: str, final_text: str):
        self.hud.finalize_chunk(chunk_id, final_text)

    def _on_pipeline_state(self, is_active: bool):
        if not is_active:
            self.hud.set_status("Done - click Start for a new session")
            self.hud.set_recording_ui(False)
            self.tray.set_idle()

    # ------------------------------------------------------------------
    # Hotkey
    # ------------------------------------------------------------------

    def _setup_hotkey(self):
        if _kb_module is None:
            return
        key_str = self.settings.get("shortcut_key") or "f9"
        target = _KEY_MAP.get(key_str.lower())
        if target is None:
            target = _KEY_MAP.get("f9")

        def on_press(key):
            if key == target:
                self.loop.call_soon_threadsafe(self._toggle_dictation)

        try:
            self._listener = _kb_module.Listener(on_press=on_press)
            self._listener.start()
        except Exception as e:
            print(f"[main] Hotkey setup failed: {e}")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _quit(self):
        self._stop_dictation()
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass
        if self._qapp:
            self._qapp.quit()

    def run(self):
        self._qapp = QApplication(sys.argv)
        self._qapp.setApplicationName("DictaThesis")
        self._qapp.setQuitOnLastWindowClosed(True)

        self.loop = qasync.QEventLoop(self._qapp)
        asyncio.set_event_loop(self.loop)

        # Ctrl+C: pump a timer so Python can handle signals
        signal.signal(signal.SIGINT, lambda *_: self._quit())
        timer = QTimer()
        timer.timeout.connect(lambda: None)
        timer.start(200)

        # Settings (can open before HUD exists)
        self.settings_ui = SettingsWindow(self.settings)

        # Pipeline
        self.pipeline = Pipeline(
            settings=self.settings,
            on_draft=self._on_draft,
            on_final=self._on_final,
            on_state_change=self._on_pipeline_state,
        )

        # Audio
        self.audio = AudioCapture(
            on_chunk=self.pipeline.on_chunk,
            loop=self.loop,
            vad_silence_duration=self.settings.get("vad_silence_duration"),
            vad_mode=self.settings.get("vad_mode"),
        )

        # HUD
        self.hud = HUD(
            on_start=self._start_dictation,
            on_stop=self._stop_dictation,
            on_settings=self.settings_ui.open,
            on_quit=self._quit,
        )
        self.hud.show()
        self.hud.raise_()
        self.hud.activateWindow()

        # Tray (best effort - may not be visible on WSLg)
        self.tray = TrayIcon(
            on_toggle=self._toggle_dictation,
            on_settings=self.settings_ui.open,
            on_quit=self._quit,
            settings=self.settings,
        )
        self.tray.run()

        # Hotkey (skipped on WSL2)
        self._setup_hotkey()

        shortcut = self.settings.get("shortcut_key").upper() if _kb_module else "N/A (WSL2)"
        api_status = "configured" if self.settings.get("api_key") else "NOT SET"
        print(
            f"DictaThesis started.\n"
            f"  Shortcut : {shortcut}\n"
            f"  Language : {self.settings.get('language')}\n"
            f"  API key  : {api_status}\n",
            flush=True,
        )

        with self.loop:
            self.loop.run_forever()


def main():
    DictaThesis().run()


if __name__ == "__main__":
    main()
