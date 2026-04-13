"""
DictaThesis — entry point.

Threading model
---------------
  Main thread    : pystray tray icon (blocks with icon.run())
  asyncio thread : event loop for API calls and pipeline orchestration
  pynput thread  : global keyboard listener (auto-created by pynput)
  hud thread     : tkinter HUD window (daemon)
  sounddevice    : audio callback thread (auto-created by sounddevice)
"""
from __future__ import annotations
import asyncio
import platform
import sys
import threading

from pynput import keyboard as kb_module

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
    "f1": kb_module.Key.f1, "f2": kb_module.Key.f2,
    "f3": kb_module.Key.f3, "f4": kb_module.Key.f4,
    "f5": kb_module.Key.f5, "f6": kb_module.Key.f6,
    "f7": kb_module.Key.f7, "f8": kb_module.Key.f8,
    "f9": kb_module.Key.f9, "f10": kb_module.Key.f10,
    "f11": kb_module.Key.f11, "f12": kb_module.Key.f12,
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


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

class DictaThesis:
    def __init__(self):
        self.settings = SettingsStore()
        self.loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
        self._recording = False
        self._listener: kb_module.Listener | None = None

        # Build components
        self.hud = HUD(on_stop=self._stop_dictation)
        self.settings_ui = SettingsWindow(self.settings)

        self.pipeline = Pipeline(
            settings=self.settings,
            on_draft=self._on_draft,
            on_final=self._on_final,
            on_state_change=self._on_pipeline_state,
        )

        self.audio = AudioCapture(
            on_chunk=self.pipeline.on_chunk,
            loop=self.loop,
            vad_silence_duration=self.settings.get("vad_silence_duration"),
            vad_mode=self.settings.get("vad_mode"),
        )

        self.tray = TrayIcon(
            on_toggle=self._toggle_dictation,
            on_settings=self.settings_ui.open,
            on_quit=self._quit,
            settings=self.settings,
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
        # Idle status will be set when injection worker finishes (on_pipeline_state)

    # ------------------------------------------------------------------
    # Pipeline callbacks (called from asyncio thread)
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
    # Keyboard listener
    # ------------------------------------------------------------------

    def _setup_hotkey(self):
        key_str = self.settings.get("shortcut_key") or "f9"
        target_key = _resolve_hotkey(key_str)
        if target_key is None:
            print(f"[main] Unknown shortcut key '{key_str}', defaulting to F9")
            target_key = kb_module.Key.f9

        def on_press(key):
            if key == target_key:
                # Schedule toggle on the asyncio loop (non-blocking from pynput thread)
                self.loop.call_soon_threadsafe(self._toggle_dictation)

        self._listener = kb_module.Listener(on_press=on_press)
        self._listener.start()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _quit(self):
        self._stop_dictation()
        if self._listener:
            self._listener.stop()
        self.loop.call_soon_threadsafe(self.loop.stop)
        if self.tray._icon:
            self.tray._icon.stop()

    def run(self):
        # 1. Start asyncio event loop in background thread
        loop_thread = threading.Thread(
            target=self.loop.run_forever, name="asyncio-loop", daemon=True
        )
        loop_thread.start()

        # 2. Start HUD in background thread
        hud_thread = threading.Thread(target=self.hud.run, name="hud", daemon=True)
        hud_thread.start()
        self.hud.wait_ready(timeout=5.0)

        # 3. Set up global hotkey listener
        self._setup_hotkey()

        print(
            f"DictaThesis started.\n"
            f"  Shortcut : {self.settings.get('shortcut_key').upper()}\n"
            f"  Language : {self.settings.get('language')}\n"
            f"  Mode     : {self.settings.get('mode')}\n"
            f"  API key  : {'configured' if self.settings.get('api_key') else 'NOT SET — open Settings'}\n"
        )

        # 4. Run tray icon in main thread (blocks until quit)
        self.tray.run()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    # On macOS, pynput keyboard listener requires the process to have
    # accessibility permissions (System Preferences → Security → Accessibility).
    if platform.system() == "Darwin":
        print("[main] On macOS: grant Accessibility permission if hotkey doesn't work.")

    app = DictaThesis()
    app.run()


if __name__ == "__main__":
    main()
