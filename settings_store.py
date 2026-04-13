"""
Persistent JSON config for DictaThesis.
Stored at ~/.config/dictathesis/config.json (Linux/macOS)
         %APPDATA%/DictaThesis/config.json (Windows)
"""

import json
import os
import platform
from pathlib import Path

DEFAULTS = {
    "api_key": "",
    "language": "fr",  # "fr" | "en" | "auto"
    "mode": "normal",  # "normal" | "equation"
    "shortcut_key": "f9",
    "vad_silence_duration": 1.5,  # seconds of silence before chunk emitted
    "vad_mode": 2,  # webrtcvad aggressiveness: 0–3
    "vocabulary": [],  # list of custom terms (strings)
    "bibliography": "",  # raw text of bibliography
    "hud_geometry": "420x220+60+60",
    "hud_opacity": 0.92,
    "inject_delay": 0.08,  # seconds to wait after clipboard write before paste
}


def _config_dir() -> Path:
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home()))
        return base / "DictaThesis"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        return base / "dictathesis"


def _config_path() -> Path:
    return _config_dir() / "config.json"


class SettingsStore:
    def __init__(self):
        self._path = _config_path()
        self._data: dict = {}
        self._load()

    def _load(self):
        if self._path.exists():
            try:
                with open(self._path, encoding="utf-8") as f:
                    loaded = json.load(f)
                self._data = {**DEFAULTS, **loaded}
            except (json.JSONDecodeError, OSError):
                self._data = dict(DEFAULTS)
        else:
            self._data = dict(DEFAULTS)

    def save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)

    def get(self, key: str):
        return self._data.get(key, DEFAULTS.get(key))

    def set(self, key: str, value):
        self._data[key] = value
        self.save()

    def update(self, updates: dict):
        self._data.update(updates)
        self.save()

    def get_vocabulary_text(self) -> str:
        """Return vocabulary as newline-separated string for display in settings."""
        return "\n".join(self._data.get("vocabulary", []))

    def set_vocabulary_from_text(self, text: str):
        terms = [t.strip() for t in text.splitlines() if t.strip()]
        self.set("vocabulary", terms)
