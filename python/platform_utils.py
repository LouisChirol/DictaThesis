"""
Shared platform detection utilities for DictaThesis.
"""

from __future__ import annotations

import platform
import shutil


def _is_wsl() -> bool:
    if platform.system() != "Linux":
        return False
    try:
        with open("/proc/version") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


IS_WSL = _is_wsl()
IS_MACOS = platform.system() == "Darwin"
IS_WINDOWS = platform.system() == "Windows"
USE_WIN_INJECT = IS_WSL and shutil.which("clip.exe") is not None
