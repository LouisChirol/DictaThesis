"""
Text field editing via keyboard simulation.

Provides operations like delete-backwards and replace-range that work by
simulating key sequences (Shift+Left to select, Backspace to delete, etc.)
in the currently focused application.

Limitation: only reliable for text injected during the current session that
the user has not manually edited since injection.
"""

from __future__ import annotations

import re
import subprocess
import time

from platform_utils import USE_WIN_INJECT


class TextFieldEditor:
    """Simulates text editing operations via keyboard shortcuts."""

    def delete_backwards(self, char_count: int) -> None:
        """Select char_count characters backwards from cursor, then delete."""
        if char_count <= 0:
            return
        if USE_WIN_INJECT:
            self._win_select_left(char_count)
            self._win_send_key("{BACKSPACE}")
        else:
            self._native_select_left(char_count)
            self._native_send_backspace()

    def replace_backwards(self, char_count: int, new_text: str) -> None:
        """Select char_count characters backwards, then replace with new_text."""
        if char_count <= 0:
            return
        self.delete_backwards(char_count)
        time.sleep(0.05)
        # Inject replacement text via clipboard
        from injector import inject_text
        inject_text(new_text)

    # ------------------------------------------------------------------
    # Sentence / word boundary helpers
    # ------------------------------------------------------------------

    @staticmethod
    def find_last_sentence_length(buffer: str) -> int:
        """Find the character count of the last sentence in the buffer."""
        stripped = buffer.rstrip()
        if not stripped:
            return 0
        # Find the last sentence-ending punctuation before the final one
        # Look for .!? followed by space, or start of string
        matches = list(re.finditer(r'[.!?]\s+', stripped[:-1]))
        if matches:
            last_match = matches[-1]
            sentence_start = last_match.end()
        else:
            sentence_start = 0
        # Count from sentence_start to end of buffer (including trailing whitespace)
        return len(buffer) - sentence_start

    @staticmethod
    def find_last_word_length(buffer: str) -> int:
        """Find the character count of the last word in the buffer (including preceding space)."""
        stripped = buffer.rstrip()
        if not stripped:
            return 0
        # Find the last word boundary
        match = re.search(r'\s(\S+)$', stripped)
        if match:
            # Include the space before the word
            return len(buffer) - match.start()
        # Entire buffer is one word
        return len(buffer)

    @staticmethod
    def find_word_offset(buffer: str, word: str) -> tuple[int, int] | None:
        """
        Find the last occurrence of `word` in buffer.
        Returns (offset_from_end, length) or None if not found.
        """
        # Case-insensitive search for the last occurrence
        idx = buffer.lower().rfind(word.lower())
        if idx == -1:
            return None
        offset_from_end = len(buffer) - idx
        return (offset_from_end, len(word))

    # ------------------------------------------------------------------
    # Windows (WSL2) keyboard simulation
    # ------------------------------------------------------------------

    def _win_send_key(self, key: str) -> None:
        """Send a key via PowerShell SendKeys."""
        script = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            f"[System.Windows.Forms.SendKeys]::SendWait('{key}')"
        )
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; " + script],
            timeout=5,
        )

    def _win_select_left(self, count: int) -> None:
        """Select `count` characters to the left using Shift+Left via SendKeys."""
        # SendKeys uses +{LEFT N} for Shift+Left repeated N times
        # Batch in groups to avoid overly long SendKeys strings
        batch_size = 50
        remaining = count
        while remaining > 0:
            n = min(remaining, batch_size)
            self._win_send_key(f"+{{LEFT {n}}}")
            remaining -= n
            if remaining > 0:
                time.sleep(0.02)

    # ------------------------------------------------------------------
    # Native (Linux X11, macOS) keyboard simulation
    # ------------------------------------------------------------------

    def _native_select_left(self, count: int) -> None:
        """Select `count` characters to the left using pynput Shift+Left."""
        from pynput.keyboard import Controller, Key

        kb = Controller()
        kb.press(Key.shift)
        for _ in range(count):
            kb.press(Key.left)
            kb.release(Key.left)
        kb.release(Key.shift)
        time.sleep(0.02)

    def _native_send_backspace(self) -> None:
        """Send a Backspace key."""
        from pynput.keyboard import Controller, Key

        kb = Controller()
        kb.press(Key.backspace)
        kb.release(Key.backspace)
        time.sleep(0.02)
