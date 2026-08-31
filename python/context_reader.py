"""
Read text from the currently focused text field (best-effort, cross-platform).

Used at session start to seed the LLM with document context. Returns None if
the platform doesn't support it or the focused element isn't a text field.
Foreground window id/title are used to log whether a later paragraph rewrite
still owns the caret (best-effort; browsers often cannot expose field text).
"""

from __future__ import annotations

import shutil
import subprocess

from platform_utils import IS_MACOS, IS_WINDOWS, IS_WSL


def read_focused_text(timeout: float = 5.0) -> str | None:
    """
    Attempt to read text from the currently focused UI element.

    Returns the text content or None if unavailable/unsupported.
    """
    try:
        if IS_WSL or IS_WINDOWS:
            return _read_windows_uiautomation(timeout=timeout)
        elif IS_MACOS:
            return _read_macos_accessibility(timeout=timeout)
        else:
            return None
    except Exception as e:
        print(f"[context_reader] Failed to read focused text: {e}")
        return None


def get_foreground_window_info() -> tuple[str | None, str | None]:
    """Return (window_id, title) for the foreground window, if available."""
    try:
        if IS_WINDOWS:
            return _windows_foreground_info()
        if IS_WSL:
            return _wsl_foreground_info()
        if IS_MACOS:
            return _macos_foreground_info()
    except Exception as e:
        print(f"[context_reader] Foreground window query failed: {e}")
    return None, None


def _windows_foreground_info() -> tuple[str | None, str | None]:
    import ctypes

    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None, None
    length = user32.GetWindowTextLengthW(hwnd) + 1
    buf = ctypes.create_unicode_buffer(length)
    user32.GetWindowTextW(hwnd, buf, length)
    return str(int(hwnd)), buf.value or ""


def _wsl_foreground_info() -> tuple[str | None, str | None]:
    if not shutil.which("powershell.exe"):
        return None, None
    script = """\
Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public class FgWin {
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll", CharSet=CharSet.Unicode)]
  public static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);
}
"@
$h = [FgWin]::GetForegroundWindow()
$sb = New-Object System.Text.StringBuilder 512
[void][FgWin]::GetWindowText($h, $sb, $sb.Capacity)
Write-Output ("{0}`t{1}" -f [int64]$h, $sb.ToString())
"""
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", script],
            capture_output=True,
            timeout=2,
        )
        if result.returncode != 0:
            return None, None
        line = result.stdout.decode("utf-8", errors="replace").strip()
        if not line:
            return None, None
        wid, _, title = line.partition("\t")
        return (wid or None), (title or "")
    except (subprocess.TimeoutExpired, OSError):
        return None, None


def _macos_foreground_info() -> tuple[str | None, str | None]:
    script = (
        'tell application "System Events" to '
        "get {unix id, name} of first process whose frontmost is true"
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            timeout=2,
        )
        if result.returncode != 0:
            return None, None
        raw = result.stdout.decode("utf-8", errors="replace").strip()
        # osascript typically returns "123, AppName"
        if "," in raw:
            wid, title = raw.split(",", 1)
            return wid.strip(), title.strip()
        return raw or None, ""
    except (subprocess.TimeoutExpired, OSError):
        return None, None


def _read_windows_uiautomation(timeout: float = 5.0) -> str | None:
    """Use PowerShell UIAutomation to read the focused element's text."""
    ps_exe = "powershell.exe" if IS_WSL else "powershell"
    if IS_WSL and not shutil.which("powershell.exe"):
        return None

    script = """\
Add-Type -AssemblyName UIAutomationClient
$focused = [System.Windows.Automation.AutomationElement]::FocusedElement
if ($focused -eq $null) { exit 1 }
$valuePattern = $null
$hasValue = $focused.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$valuePattern)
if ($hasValue -and $valuePattern -ne $null) {
    Write-Output $valuePattern.Current.Value
    exit 0
}
$textPattern = $null
$hasText = $focused.TryGetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern, [ref]$textPattern)
if ($hasText -and $textPattern -ne $null) {
    $range = $textPattern.DocumentRange
    Write-Output $range.GetText(-1)
    exit 0
}
exit 1
"""
    try:
        result = subprocess.run(
            [ps_exe, "-NoProfile", "-Command", script],
            capture_output=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            text = result.stdout.decode("utf-8", errors="replace").rstrip("\r\n")
            return text if text else None
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def _read_macos_accessibility(timeout: float = 5.0) -> str | None:
    """Use osascript to read the focused UI element's value via Accessibility API."""
    script = (
        'tell application "System Events" to '
        "get value of first text field of "
        "(first process whose frontmost is true)"
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            text = result.stdout.decode("utf-8", errors="replace").rstrip("\n")
            return text if text else None
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None
