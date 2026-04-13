"""
DictaThesis — entry point.

Threading model
---------------
  Main thread    : Qt event loop + asyncio (via qasync.QEventLoop)
  pynput thread  : global keyboard listener (auto-created by pynput)
  sounddevice    : audio callback thread (auto-created by sounddevice)
"""

from __future__ import annotations

import asyncio
import platform
import sys

import qasync
from pynput import keyboard as kb_module
from PySide6.QtWidgets import QApplication

from audio import AudioCapture
from hud import HUD
from pipeline import Pipeline
from settings_store import SettingsStore
from settings_ui import SettingsWindow
from tray import TrayIcon

# ---------------------------------------------------------------------------
# Key mapping: config string → pynput Key
# ---------------------------------------------------------------------------

_KEY_MAP: dict[str, object] = {
    "f1": kb_module.Key.f1,
    "f2": kb_module.Key.f2,
    "f3": kb_module.Key.f3,
    "f4": kb_module.Key.f4,
    "f5": kb_module.Key.f5,
    "f6": kb_module.Key.f6,
    "f7": kb_module.Key.f7,
    "f8": kb_module.Key.f8,
    "f9": kb_module.Key.f9,
    "f10": kb_module.Key.f10,
    "f11": kb_module.Key.f11,
    "f12": kb_module.Key.f12,
    "scroll_lock": kb_module.Key.scroll_lock,
    "pause": kb_module.Key.pause,
    "insert": kb_module.Key.insert,
    "home": kb_module.Key.home,
    "end": kb_module.Key.end,
    "page_up": kb_module.Key.page_up,
    "page_down": kb_module.Key.page_down,
}


def _resolve_hotkey(key_str: str) -> object | None:
    return _KEY_MAP.get(key_str.lower())


def _is_wsl2() -> bool:
    try:
        with open("/proc/version") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


class DictaThesis:
    def __init__(self):
        self.settings = SettingsStore()
        self.loop: asyncio.AbstractEventLoop | None = None
        self._recording = False
        self._listener: kb_module.Listener | None = None

        # Settings UI and pipeline are created in __init__ so they can be
        # referenced before run() sets up the Qt objects.
        self.settings_ui = SettingsWindow(self.settings)

        self.pipeline = Pipeline(
            settings=self.settings,
            on_draft=self._on_draft,
            on_final=self._on_final,
            on_state_change=self._on_pipeline_state,
        )

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
            print("[main] No API key configured. Open Settings to add one.")
            self.settings_ui.open()
            return

        self._recording = True
        self.pipeline.start_session()
        self.audio.start()
        self.hud.clear()
        self.hud.set_status("🔴  Recording…  (F9 or Stop button to end)")
        self.tray.set_recording(True)

    def _stop_dictation(self):
        if not self._recording:
            return
        self._recording = False
        self.audio.stop()
        self.pipeline.stop_session()
        self.hud.set_status("⏳  Finishing…  (processing remaining chunks)")
        self.tray.set_processing()

    # ------------------------------------------------------------------
    # Pipeline callbacks (called from asyncio/main thread via qasync)
    # ------------------------------------------------------------------

    def _on_draft(self, chunk_id: str, draft_text: str):
        self.hud.add_chunk(chunk_id, draft_text)

    def _on_final(self, chunk_id: str, final_text: str):
        self.hud.finalize_chunk(chunk_id, final_text)

    def _on_pipeline_state(self, is_active: bool):
        if not is_active:
            self.hud.set_status("✓  Done  ·  F9 to start a new session")
            self.tray.set_idle()

    # ------------------------------------------------------------------
    # Keyboard listener (pynput thread → main thread via call_soon_threadsafe)
    # ------------------------------------------------------------------

    def _setup_hotkey(self):
        if _is_wsl2():
            print(
                "[main] WSL2 detected. Global hotkeys via pynput may not work "
                "under Wayland — try running with DISPLAY set (XWayland)."
            )

        key_str = self.settings.get("shortcut_key") or "f9"
        target_key = _resolve_hotkey(key_str)
        if target_key is None:
            print(f"[main] Unknown shortcut key '{key_str}', defaulting to F9")
            target_key = kb_module.Key.f9

        def on_press(key):
            if key == target_key:
                self.loop.call_soon_threadsafe(self._toggle_dictation)

        try:
            self._listener = kb_module.Listener(on_press=on_press)
            self._listener.start()
        except Exception as e:
            print(f"[main] Could not start keyboard listener: {e}")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _quit(self):
        self._stop_dictation()
        if self._listener:
            self._listener.stop()
        if self.loop:
            self.loop.stop()

    def run(self):
        app = QApplication(sys.argv)
        app.setQuitOnLastWindowClosed(False)  # keep running when HUD is closed

        self.loop = qasync.QEventLoop(app)
        asyncio.set_event_loop(self.loop)

        # Build GUI components (must happen after QApplication exists)
        self.hud = HUD(on_stop=self._stop_dictation)
        self.tray = TrayIcon(
            on_toggle=self._toggle_dictation,
            on_settings=self.settings_ui.open,
            on_quit=self._quit,
            settings=self.settings,
        )

        # AudioCapture needs the event loop reference
        self.audio = AudioCapture(
            on_chunk=self.pipeline.on_chunk,
            loop=self.loop,
            vad_silence_duration=self.settings.get("vad_silence_duration"),
            vad_mode=self.settings.get("vad_mode"),
        )

        self.hud.show()
        self.tray.run()   # non-blocking: just shows the system tray icon
        self._setup_hotkey()

        print(
            f"DictaThesis started.\n"
            f"  Shortcut : {self.settings.get('shortcut_key').upper()}\n"
            f"  Language : {self.settings.get('language')}\n"
            f"  Mode     : {self.settings.get('mode')}\n"
            f"  API key  : {'configured' if self.settings.get('api_key') else 'NOT SET'}"
            " — open Settings from the tray menu\n"
        )

        with self.loop:
            self.loop.run_forever()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    if platform.system() == "Darwin":
        print("[main] On macOS: grant Accessibility permission if hotkey doesn't work.")

    app = DictaThesis()
    app.run()


if __name__ == "__main__":
    main()
