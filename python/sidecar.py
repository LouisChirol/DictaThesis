"""
DictaThesis sidecar — headless IPC bridge for the Electron frontend.

Communicates via newline-delimited JSON (JSONL) over stdin/stdout.
All diagnostic prints go to stderr so stdout stays clean for protocol.

Commands (stdin):
    {"cmd": "start_dictation"}
    {"cmd": "stop_dictation"}
    {"cmd": "update_settings", "data": {...}}
    {"cmd": "get_settings"}
    {"cmd": "quit"}

Events (stdout):
    {"event": "ready"}
    {"event": "chunk_update", "chunk_id": "...", "state": "draft"|"final", "text": "..."}
    {"event": "status_change", "status": "idle"|"recording"|"processing", "message": "..."}
    {"event": "settings", "data": {...}}
    {"event": "error", "message": "..."}
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import signal
import sys

# ── Redirect stdout to stderr BEFORE any imports that might print ──
# Keep a handle to the real fd 1 for protocol output.
_proto_fd = os.dup(1)
if hasattr(sys.stderr, "reconfigure"):
    # Force UTF-8 so Electron (which decodes as UTF-8) receives valid text.
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
sys.stdout = sys.stderr  # print() now goes to stderr
_proto_file = os.fdopen(
    _proto_fd,
    "w",
    buffering=1,
    encoding="utf-8",
    errors="replace",
)  # line-buffered protocol output

from audio import AudioCapture
from pipeline import Pipeline
from settings_store import SettingsStore


def _is_wsl() -> bool:
    if platform.system() != "Linux":
        return False
    try:
        with open("/proc/version") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


_IS_WSL = _is_wsl()


def _configure_windows_event_loop_policy() -> None:
    """
    Work around Windows asyncio pipe transport issues on newer Python versions.

    The sidecar relies on stdin/stdout pipes for IPC with Electron; using the
    selector policy is more stable than proactor for this workload.
    """
    if platform.system() != "Windows":
        return
    policy_cls = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if policy_cls is None:
        return
    asyncio.set_event_loop_policy(policy_cls())


class Sidecar:
    def __init__(self, *, enable_hotkey: bool = True):
        self.settings = SettingsStore()
        self.loop: asyncio.AbstractEventLoop | None = None
        self._recording = False
        self._enable_hotkey = enable_hotkey
        self._listener = None
        self._shutdown_requested = False
        self._reader_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Protocol I/O
    # ------------------------------------------------------------------

    def _emit(self, event: dict):
        """Write a JSON event to the protocol stream (fd 1)."""
        # Keep protocol payload ASCII-only to avoid cross-encoding issues on
        # Windows pipes; JSON escapes are decoded back to Unicode in Electron.
        line = json.dumps(event, ensure_ascii=True)
        _proto_file.write(line + "\n")
        _proto_file.flush()

    async def _read_commands(self):
        """Read JSONL commands from stdin until EOF."""
        stream = sys.stdin.buffer if hasattr(sys.stdin, "buffer") else sys.stdin
        while True:
            # Reading stdin from a worker thread is more reliable than
            # connect_read_pipe() across Windows/Electron combinations.
            line = await asyncio.to_thread(stream.readline)
            if not line:
                print("[sidecar] stdin EOF")
                break

            if isinstance(line, bytes):
                line = line.decode("utf-8", errors="replace")
            line = line.strip()
            if not line:
                continue

            try:
                msg = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"[sidecar] Bad JSON from stdin: {e}")
                continue
            self._handle_command(msg)

    def _on_reader_done(self, task: asyncio.Task):
        if self._shutdown_requested:
            return
        if task.cancelled():
            print("[sidecar] command reader cancelled")
            return
        else:
            exc = task.exception()
            if exc:
                print(f"[sidecar] command reader crashed: {exc}")
            else:
                print("[sidecar] command reader ended")

        # If stdin is gone, restarting would just spin in a crash loop.
        self._quit()

    def _handle_command(self, msg: dict):
        cmd = msg.get("cmd")
        if cmd == "start_dictation":
            self._start_dictation()
        elif cmd == "stop_dictation":
            self._stop_dictation()
        elif cmd == "update_settings":
            data = msg.get("data", {})
            self.settings.update(data)
            self._emit({"event": "settings", "data": self._get_all_settings()})
        elif cmd == "get_settings":
            self._emit({"event": "settings", "data": self._get_all_settings()})
        elif cmd == "quit":
            self._shutdown_requested = True
            self._quit()
        else:
            print(f"[sidecar] Unknown command: {cmd}")

    def _get_all_settings(self) -> dict:
        keys = [
            "api_key", "language", "mode", "shortcut_key",
            "vad_silence_duration", "max_chunk_duration", "vad_backend", "vad_mode", "vocabulary",
            "bibliography", "enable_injection",
        ]
        return {k: self.settings.get(k) for k in keys}

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
            self._emit({
                "event": "error",
                "message": "No API key configured — open Settings first",
            })
            return

        self.pipeline.start_session()
        try:
            self.audio.start()
        except Exception as e:
            self.pipeline.stop_session()
            # Provide helpful message for common audio issues
            msg = str(e)
            if "device" in msg.lower() or "portaudio" in msg.lower():
                try:
                    import sounddevice as sd
                    devices = sd.query_devices()
                    print(f"[sidecar] Available audio devices:\n{devices}")
                except Exception:
                    pass
                msg = (
                    f"Audio error: {e}\n"
                    "No microphone found. On WSL2, install PulseAudio:\n"
                    "  sudo apt install pulseaudio && pulseaudio --start"
                )
            else:
                msg = f"Audio error: {e}"
            self._emit({"event": "error", "message": msg})
            print(f"[sidecar] {msg}")
            return

        self._recording = True
        self._emit({
            "event": "status_change",
            "status": "recording",
            "message": "Recording... (press Stop to end)",
        })

    def _stop_dictation(self):
        if not self._recording:
            return
        self._recording = False
        self.audio.stop()
        self.pipeline.stop_session()
        self._emit({
            "event": "status_change",
            "status": "processing",
            "message": "Finishing... processing remaining chunks",
        })

    # ------------------------------------------------------------------
    # Pipeline callbacks
    # ------------------------------------------------------------------

    def _on_draft(self, chunk_id: str, draft_text: str):
        self._emit({
            "event": "chunk_update",
            "chunk_id": chunk_id,
            "state": "draft",
            "text": draft_text,
        })

    def _on_final(self, chunk_id: str, final_text: str):
        self._emit({
            "event": "chunk_update",
            "chunk_id": chunk_id,
            "state": "final",
            "text": final_text,
        })

    def _on_pipeline_state(self, is_active: bool):
        if not is_active:
            self._emit({
                "event": "status_change",
                "status": "idle",
                "message": "Done — click Start for a new session",
            })

    # ------------------------------------------------------------------
    # Hotkey (optional — Electron handles shortcuts when --no-hotkey)
    # ------------------------------------------------------------------

    def _setup_hotkey(self):
        if not self._enable_hotkey:
            return
        if _IS_WSL:
            return

        try:
            from pynput import keyboard as kb
        except Exception:
            print("[sidecar] pynput not available — hotkey disabled")
            return

        key_map = {f"f{i}": getattr(kb.Key, f"f{i}") for i in range(1, 13)}
        for name in ("scroll_lock", "pause", "insert", "home", "end", "page_up", "page_down"):
            key_map[name] = getattr(kb.Key, name)

        key_str = self.settings.get("shortcut_key") or "f9"
        target = key_map.get(key_str.lower(), key_map.get("f9"))

        def on_press(key):
            if key == target:
                self.loop.call_soon_threadsafe(self._toggle_dictation)

        try:
            self._listener = kb.Listener(on_press=on_press)
            self._listener.start()
        except Exception as e:
            print(f"[sidecar] Hotkey setup failed: {e}")

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
        # Cancel pending tasks to avoid "Task was destroyed" warnings
        if self.loop:
            for task in asyncio.all_tasks(self.loop):
                task.cancel()
            if self.loop.is_running():
                self.loop.call_soon_threadsafe(self.loop.stop)

    def run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        # Ctrl+C
        if platform.system() != "Windows":
            self.loop.add_signal_handler(signal.SIGINT, self._quit)
            self.loop.add_signal_handler(signal.SIGTERM, self._quit)

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
            max_chunk_duration=self.settings.get("max_chunk_duration"),
            vad_backend=self.settings.get("vad_backend"),
            vad_mode=self.settings.get("vad_mode"),
        )

        # Hotkey
        self._setup_hotkey()

        shortcut = (self.settings.get("shortcut_key") or "f9").upper()
        if not self._enable_hotkey:
            shortcut += " (managed by Electron)"
        api_status = "configured" if self.settings.get("api_key") else "NOT SET"
        print(
            f"DictaThesis sidecar started.\n"
            f"  Shortcut : {shortcut}\n"
            f"  Language : {self.settings.get('language')}\n"
            f"  API key  : {api_status}",
        )

        # Signal ready to Electron
        self._emit({"event": "ready"})

        # Start reading commands from stdin
        self._reader_task = self.loop.create_task(self._read_commands())
        self._reader_task.add_done_callback(self._on_reader_done)
        self.loop.run_forever()


def main():
    _configure_windows_event_loop_policy()

    parser = argparse.ArgumentParser(description="DictaThesis sidecar process")
    parser.add_argument(
        "--no-hotkey",
        action="store_true",
        help="Disable pynput hotkey (Electron manages global shortcuts)",
    )
    args = parser.parse_args()

    Sidecar(enable_hotkey=not args.no_hotkey).run()


if __name__ == "__main__":
    main()
