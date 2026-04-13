"""
Text injection at cursor position in the currently focused application.
Strategy: write text to clipboard, simulate Ctrl+V (or Cmd+V on macOS).
This works universally in any application — Word, TeXStudio, browsers, etc.
"""
import platform
import time
import threading

import pyperclip
from pynput.keyboard import Controller, Key

_kb = Controller()
_lock = threading.Lock()  # prevent concurrent injections corrupting clipboard

IS_MACOS = platform.system() == "Darwin"
PASTE_MODIFIER = Key.cmd if IS_MACOS else Key.ctrl


def inject_text(text: str, delay: float = 0.08):
    """
    Inject text at the cursor position of the currently focused app.
    Temporarily overwrites the clipboard, then restores it.

    Args:
        text: The text to inject.
        delay: Seconds to wait after clipboard write before simulating paste.
               Increase if paste doesn't work reliably on slow machines.
    """
    if not text:
        return

    with _lock:
        try:
            saved = _safe_paste()
            pyperclip.copy(text)
            time.sleep(delay)
            _simulate_paste()
            time.sleep(0.05)
            if saved is not None:
                pyperclip.copy(saved)
        except Exception as e:
            # Fail silently — don't crash the pipeline over injection errors
            print(f"[injector] Error: {e}")


def _simulate_paste():
    _kb.press(PASTE_MODIFIER)
    _kb.press("v")
    _kb.release("v")
    _kb.release(PASTE_MODIFIER)


def _safe_paste() -> str | None:
    """Read current clipboard content, returning None on failure."""
    try:
        return pyperclip.paste()
    except Exception:
        return None
