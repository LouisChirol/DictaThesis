"""
Text injection at cursor position in the currently focused application.

Strategy: write text to clipboard, simulate Ctrl+V (or Cmd+V on macOS).
This works universally in any application — Word, TeXStudio, browsers, etc.

WSL2 special handling: uses clip.exe for the Windows clipboard and
powershell.exe SendKeys for the paste keystroke, since pynput/pyperclip
only reach X11 apps under WSLg, not native Windows apps.
"""

import subprocess
import threading
import time

from platform_utils import IS_MACOS, USE_WIN_INJECT

_lock = threading.Lock()  # prevent concurrent injections corrupting clipboard


def inject_text(text: str, delay: float = 0.08):
    """
    Inject text at the cursor position of the currently focused app.
    Temporarily overwrites the clipboard, then restores it.
    """
    if not text:
        return

    with _lock:
        try:
            if USE_WIN_INJECT:
                _inject_windows(text, delay)
            else:
                _inject_native(text, delay)
        except Exception as e:
            print(f"[injector] Error: {e}")


# ---------------------------------------------------------------------------
# Windows injection (WSL2 → Windows apps)
# ---------------------------------------------------------------------------


def _ps_command(script: str, **kwargs) -> subprocess.CompletedProcess:
    """Run a powershell command with UTF-8 encoding."""
    return subprocess.run(
        [
            "powershell.exe", "-NoProfile", "-Command",
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; " + script,
        ],
        **kwargs,
        timeout=5,
    )


def _inject_windows(text: str, delay: float):
    """Use powershell Set-Clipboard + SendKeys for Windows-native paste."""
    # Save current Windows clipboard
    saved = _win_clipboard_read()

    # Write to Windows clipboard via powershell (proper Unicode support)
    # Escape single quotes for powershell string
    escaped = text.replace("'", "''")
    _ps_command(f"Set-Clipboard -Value '{escaped}'", check=True)
    time.sleep(delay)

    # Simulate Ctrl+V via powershell SendKeys
    _ps_command(
        "Add-Type -AssemblyName System.Windows.Forms; "
        "[System.Windows.Forms.SendKeys]::SendWait('^v')",
        check=True,
    )
    time.sleep(0.05)

    # Restore clipboard
    if saved is not None:
        _win_clipboard_write(saved)


def _win_clipboard_read() -> str | None:
    """Read current Windows clipboard content."""
    try:
        result = _ps_command("Get-Clipboard", capture_output=True)
        return result.stdout.decode("utf-8", errors="replace").rstrip("\r\n") if result.returncode == 0 else None
    except Exception:
        return None


def _win_clipboard_write(text: str):
    """Write clipboard safely, including empty-string restoration."""
    try:
        if text == "":
            _ps_command("Set-Clipboard -Value ([string]::Empty)", check=False)
            return
        escaped = text.replace("'", "''")
        _ps_command(f"Set-Clipboard -Value '{escaped}'", check=False)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Native injection (Linux X11, macOS, Windows)
# ---------------------------------------------------------------------------


def _inject_native(text: str, delay: float):
    """Use pyperclip + pynput for native clipboard/keyboard injection."""
    import pyperclip
    from pynput.keyboard import Controller, Key

    kb = Controller()
    paste_mod = Key.cmd if IS_MACOS else Key.ctrl

    saved = _safe_paste_native()
    pyperclip.copy(text)
    time.sleep(delay)

    # Simulate Ctrl+V / Cmd+V
    kb.press(paste_mod)
    kb.press("v")
    kb.release("v")
    kb.release(paste_mod)
    time.sleep(0.05)

    if saved is not None:
        pyperclip.copy(saved)


def _safe_paste_native() -> str | None:
    """Read current clipboard content via pyperclip."""
    try:
        import pyperclip
        return pyperclip.paste()
    except Exception:
        return None
