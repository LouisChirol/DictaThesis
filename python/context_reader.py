"""
Read text from the currently focused text field (best-effort, cross-platform).

Used at session start to seed the LLM with document context. Returns None if
the platform doesn't support it or the focused element isn't a text field.
"""

from __future__ import annotations

import shutil
import subprocess

from platform_utils import IS_MACOS, IS_WINDOWS, IS_WSL


def read_focused_text() -> str | None:
    """
    Attempt to read text from the currently focused UI element.

    Returns the text content or None if unavailable/unsupported.
    """
    try:
        if IS_WSL or IS_WINDOWS:
            return _read_windows_uiautomation()
        elif IS_MACOS:
            return _read_macos_accessibility()
        else:
            # Linux X11/Wayland — no reliable non-intrusive method
            return None
    except Exception as e:
        print(f"[context_reader] Failed to read focused text: {e}")
        return None


def _read_windows_uiautomation() -> str | None:
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
            timeout=5,
        )
        if result.returncode == 0:
            text = result.stdout.decode("utf-8", errors="replace").rstrip("\r\n")
            return text if text else None
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def _read_macos_accessibility() -> str | None:
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
            timeout=5,
        )
        if result.returncode == 0:
            text = result.stdout.decode("utf-8", errors="replace").rstrip("\n")
            return text if text else None
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None
