"""
Persistent JSON config for DictaThesis.
Stored at ~/.config/dictathesis/config.json (Linux/macOS)
         %APPDATA%/DictaThesis/config.json (Windows)
"""

import json
import os
import platform
from pathlib import Path

DEFAULT_DICTATION_COMMANDS = [
    # --- Punctuation ---
    {"id": "period", "triggers": ["point", "period", "point final"],
     "category": "formatting", "action": {"type": "insert_text", "text": "."},
     "description": "Insert a period"},
    {"id": "comma", "triggers": ["virgule", "comma"],
     "category": "formatting", "action": {"type": "insert_text", "text": ","},
     "description": "Insert a comma"},
    {"id": "newline", "triggers": ["à la ligne", "new line", "next line"],
     "category": "formatting", "action": {"type": "insert_text", "text": "\n"},
     "description": "Insert a line break"},
    {"id": "new_paragraph", "triggers": ["nouveau paragraphe", "new paragraph"],
     "category": "formatting", "action": {"type": "insert_text", "text": "\n\n"},
     "description": "Start a new paragraph"},
    # --- Headings ---
    {"id": "heading1", "triggers": ["titre un", "heading one", "title one"],
     "category": "formatting", "action": {"type": "insert_text", "text": "\n# "},
     "description": "Start heading level 1"},
    {"id": "heading2", "triggers": ["titre deux", "heading two", "title two"],
     "category": "formatting", "action": {"type": "insert_text", "text": "\n## "},
     "description": "Start heading level 2"},
    {"id": "heading3", "triggers": ["titre trois", "heading three"],
     "category": "formatting", "action": {"type": "insert_text", "text": "\n### "},
     "description": "Start heading level 3"},
    # --- Inline formatting ---
    {"id": "bold_start", "triggers": ["gras", "bold", "en gras"],
     "category": "formatting", "action": {"type": "insert_text", "text": "**"},
     "description": "Start bold text"},
    {"id": "bold_end", "triggers": ["fin gras", "end bold"],
     "category": "formatting", "action": {"type": "insert_text", "text": "**"},
     "description": "End bold text"},
    {"id": "italic_start", "triggers": ["italique", "italic"],
     "category": "formatting", "action": {"type": "insert_text", "text": "_"},
     "description": "Start italic text"},
    {"id": "italic_end", "triggers": ["fin italique", "end italic"],
     "category": "formatting", "action": {"type": "insert_text", "text": "_"},
     "description": "End italic text"},
    # --- Equations ---
    {"id": "equation_start", "triggers": ["début équation", "start equation"],
     "category": "formatting", "action": {"type": "insert_text", "text": "$"},
     "description": "Start inline equation"},
    {"id": "equation_end", "triggers": ["fin équation", "end equation"],
     "category": "formatting", "action": {"type": "insert_text", "text": "$"},
     "description": "End inline equation"},
    # --- References ---
    {"id": "bibliography_ref",
     "triggers": ["référence", "reference number", "reference", "cite"],
     "category": "formatting",
     "action": {"type": "insert_text", "text": "\\cite{ref__N__}"},
     "description": "Insert bibliography reference (N = reference number)"},
    # --- Control ---
    {"id": "stop_dictation", "triggers": ["arrêter la dictée", "stop dictation"],
     "category": "control", "action": {"type": "control", "control": "stop_dictation"},
     "description": "Stop the dictation session"},
    # --- Editing (Phase 4) ---
    {"id": "delete_previous_sentence",
     "triggers": ["supprimer la phrase précédente", "delete previous sentence"],
     "category": "editing",
     "action": {"type": "edit", "edit": "delete_previous_sentence"},
     "description": "Select and delete the previous sentence"},
    {"id": "delete_previous_word",
     "triggers": ["supprimer le mot précédent", "delete previous word"],
     "category": "editing",
     "action": {"type": "edit", "edit": "delete_previous_word"},
     "description": "Delete the previous word"},
    {"id": "correct_word",
     "triggers": ["corriger le mot", "correct the word"],
     "category": "editing",
     "action": {"type": "edit", "edit": "correct_word"},
     "description": "Correct a specific word in the previously dictated text"},
    # --- LLM-instructed ---
    {"id": "formal_rewrite",
     "triggers": ["réécrire formellement", "rewrite formally"],
     "category": "llm_instructed",
     "action": {"type": "llm_instruction",
                "instruction": "Rewrite the preceding sentence in a more formal academic register."},
     "description": "Rewrite text in a more formal register"},
]

DEFAULTS = {
    "api_key": "",
    "language": "fr",  # "fr" | "en" | "auto"
    "mode": "normal",  # "normal" | "equation"
    "shortcut_key": "f9",
    "vad_silence_duration": 1.5,  # seconds of silence before chunk emitted
    "max_chunk_duration": 6.0,  # hard cut for very long utterances
    "vad_backend": "silero",  # "energy" | "webrtc" | "silero"
    "vad_mode": 2,  # webrtcvad aggressiveness: 0–3
    "enable_injection": True,  # when false, keep text in HUD without pasting
    "vocabulary": [],  # list of custom terms (strings)
    "bibliography": "",  # raw text of bibliography
    "hud_geometry": "420x220+60+60",
    "hud_opacity": 0.92,
    "inject_delay": 0.08,  # seconds to wait after clipboard write before paste
    "dictation_commands": DEFAULT_DICTATION_COMMANDS,
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
