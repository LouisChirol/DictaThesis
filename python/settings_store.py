"""
Persistent JSON config for DictaThesis.
Stored at ~/.config/dictathesis/config.json (Linux/macOS)
         %APPDATA%/DictaThesis/config.json (Windows)
"""

import json
import os
import platform
from pathlib import Path

COMMANDS_VERSION = 3

# Distinctive voice commands — no spoken point/virgule; punctuation is inferred by the LLM.
DEFAULT_DICTATION_COMMANDS = [
    # --- Structure ---
    {
        "id": "newline",
        "triggers": [
            "à la ligne",
            "a la ligne",
            "retour à la ligne",
            "nouvelle ligne",
            "new line",
        ],
        "category": "formatting",
        "action": {"type": "insert_text", "text": "\n"},
        "description": "Insert a line break",
    },
    {
        "id": "new_paragraph",
        "triggers": ["nouveau paragraphe", "new paragraph"],
        "category": "formatting",
        "action": {"type": "insert_text", "text": "\n\n"},
        "description": "Start a new paragraph",
    },
    {
        "id": "heading1",
        "triggers": ["titre un", "heading one", "title one"],
        "category": "formatting",
        "action": {"type": "insert_text", "text": "\n# "},
        "description": "Start heading level 1",
    },
    {
        "id": "heading2",
        "triggers": ["titre deux", "heading two", "title two"],
        "category": "formatting",
        "action": {"type": "insert_text", "text": "\n## "},
        "description": "Start heading level 2",
    },
    {
        "id": "heading3",
        "triggers": ["titre trois", "heading three", "title three"],
        "category": "formatting",
        "action": {"type": "insert_text", "text": "\n### "},
        "description": "Start heading level 3",
    },
    {
        "id": "bibliography_ref",
        "triggers": ["référence", "reference number", "reference", "cite", "citation"],
        "category": "formatting",
        "action": {"type": "insert_text", "text": "\\cite{ref__N__}"},
        "description": "Insert bibliography reference (N = reference number)",
    },
    {
        "id": "equation_start",
        "triggers": ["début équation", "start equation"],
        "category": "formatting",
        "action": {"type": "insert_text", "text": "$"},
        "description": "Start inline equation",
    },
    {
        "id": "equation_end",
        "triggers": ["fin équation", "end equation"],
        "category": "formatting",
        "action": {"type": "insert_text", "text": "$"},
        "description": "End inline equation",
    },
    # --- Delimiters (spoken open/close; smart pairing handled by LLM + session stack) ---
    {
        "id": "open_paren",
        "triggers": ["ouvrir parenthèse", "open parenthesis", "parenthèse ouverte"],
        "category": "formatting",
        "action": {"type": "insert_text", "text": "("},
        "description": "Open parenthesis",
    },
    {
        "id": "close_paren",
        "triggers": ["fermer parenthèse", "close parenthesis", "fin parenthèse"],
        "category": "formatting",
        "action": {"type": "insert_text", "text": ")"},
        "description": "Close parenthesis",
    },
    {
        "id": "open_quote",
        "triggers": ["ouvrir guillemet", "guillemets", "open quote"],
        "category": "formatting",
        "action": {"type": "insert_text", "text": "«"},
        "description": "Open French quotation mark",
    },
    {
        "id": "close_quote",
        "triggers": ["fermer guillemet", "fin guillemet", "close quote"],
        "category": "formatting",
        "action": {"type": "insert_text", "text": "»"},
        "description": "Close French quotation mark",
    },
    {
        "id": "open_bracket",
        "triggers": ["ouvrir crochet", "open bracket"],
        "category": "formatting",
        "action": {"type": "insert_text", "text": "["},
        "description": "Open bracket",
    },
    {
        "id": "close_bracket",
        "triggers": ["fermer crochet", "close bracket", "fin crochet"],
        "category": "formatting",
        "action": {"type": "insert_text", "text": "]"},
        "description": "Close bracket",
    },
    # --- Control ---
    {
        "id": "stop_dictation",
        "triggers": ["arrêter la dictée", "stop dictation"],
        "category": "control",
        "action": {"type": "control", "control": "stop_dictation"},
        "description": "Stop the dictation session",
    },
    # --- Editing ---
    {
        "id": "delete_previous_sentence",
        "triggers": [
            "annuler la phrase précédente",
            "supprimer la phrase précédente",
            "delete previous sentence",
            "undo sentence",
        ],
        "category": "editing",
        "action": {"type": "edit", "edit": "delete_previous_sentence"},
        "description": "Delete the previous sentence",
    },
    {
        "id": "delete_previous_word",
        "triggers": ["supprimer le mot précédent", "delete previous word", "undo word"],
        "category": "editing",
        "action": {"type": "edit", "edit": "delete_previous_word"},
        "description": "Delete the previous word",
    },
]

LEGACY_COMMAND_IDS = frozenset(
    {
        "period",
        "comma",
        "question_mark",
        "exclamation_mark",
        "colon",
        "semicolon",
        "slash",
        "backslash",
        "bold_start",
        "bold_end",
        "italic_start",
        "italic_end",
        "correct_word",
        "formal_rewrite",
    }
)

DEFAULTS = {
    "api_key": "",
    "language": "fr",  # "fr" | "en" | "auto"
    "mode": "normal",  # "normal" | "equation"
    "shortcut_key": "f9",
    "vad_silence_duration": 0.5,  # seconds of silence before chunk emitted
    "max_chunk_duration": 6.0,  # hard cut for very long utterances
    "vad_backend": "webrtc",  # "energy" | "webrtc" | "silero"
    "vad_mode": 2,  # webrtcvad aggressiveness: 0–3
    "enable_injection": True,  # when false, keep text in HUD without pasting
    "vocabulary": [],  # list of custom terms (strings)
    "bibliography": "",  # raw text of bibliography
    "hud_geometry": "480x240+60+60",
    "hud_opacity": 0.92,
    "inject_delay": 0.08,  # seconds to wait after clipboard write before paste
    "ui_theme": "parchment",  # "mocha" | "parchment"
    "dictation_commands": DEFAULT_DICTATION_COMMANDS,
    "commands_version": COMMANDS_VERSION,
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


def _needs_commands_migration(data: dict) -> bool:
    version = data.get("commands_version", 0)
    if version < COMMANDS_VERSION:
        return True
    commands = data.get("dictation_commands") or []
    return any(cmd.get("id") in LEGACY_COMMAND_IDS for cmd in commands)


def _migrate_commands(data: dict) -> dict:
    data["dictation_commands"] = list(DEFAULT_DICTATION_COMMANDS)
    data["commands_version"] = COMMANDS_VERSION
    # Drop deprecated magic-word settings
    data.pop("strip_auto_punctuation", None)
    data.pop("magic_word", None)
    return data


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

        if _needs_commands_migration(self._data):
            self._data = _migrate_commands(self._data)
            self.save()

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
