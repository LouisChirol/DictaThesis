"""
Punctuation pre-processor for STT output.

When enabled, strips all auto-generated punctuation from the STT transcript
unless immediately preceded by the magic word. The magic word + trigger
(either a trigger word like "point" or an actual punctuation char like ".")
is replaced with the intended punctuation character.

Example with magic_word="top":
  STT:   "C'est une phrase. Que je veux continuer top point le modèle top ."
  Out:   "C'est une phrase Que je veux continuer. le modèle."

STT often inserts commas between words ("top, period"); those are tolerated.
"""

from __future__ import annotations

import re

# Characters considered auto-punctuation (stripped unless magic-word-protected)
AUTO_PUNCT_CHARS = set(".,?!:;")


def build_trigger_map(commands: list[dict]) -> dict[str, str]:
    """
    Build a map of trigger_word → punctuation_char from formatting commands
    that insert a single punctuation character.
    """
    trigger_map: dict[str, str] = {}
    for cmd in commands:
        if cmd.get("category") != "formatting":
            continue
        action = cmd.get("action", {})
        if action.get("type") != "insert_text":
            continue
        text = action.get("text", "")
        # Only map single-char punctuation (not headings, bold markers, etc.)
        if len(text) == 1 and not text.isalnum():
            for trigger in cmd.get("triggers", []):
                trigger_map[trigger.lower()] = text
    return trigger_map


def preprocess_punctuation(
    draft: str,
    magic_word: str,
    commands: list[dict],
) -> str:
    """
    Process STT output: protect magic-word punctuation, strip the rest.

    1. Find all `{magic_word} {trigger_word_or_punct_char}` patterns and
       replace them with the actual punctuation character (protected).
    2. Strip all remaining unprotected auto-punctuation characters.
    3. Clean up whitespace.
    """
    if not draft or not magic_word:
        return draft

    trigger_map = build_trigger_map(commands)
    magic_lower = magic_word.lower()

    # Also build a reverse map: punct_char → punct_char (identity, for when
    # STT already converted the trigger word to its punctuation character)
    punct_identity = {ch: ch for ch in AUTO_PUNCT_CHARS}

    # Build regex that matches: magic_word + (trigger_word | punct_char)
    # Sort triggers by length descending so longer triggers match first
    # (e.g., "point d'interrogation" before "point")
    sorted_triggers = sorted(trigger_map.keys(), key=len, reverse=True)
    escaped_triggers = [re.escape(t) for t in sorted_triggers]
    escaped_puncts = [re.escape(ch) for ch in AUTO_PUNCT_CHARS]

    # The alternatives: trigger words OR single punct chars
    alternatives = escaped_triggers + escaped_puncts
    if not alternatives:
        return draft

    alt_pattern = "|".join(alternatives)
    # Match magic_word (case-insensitive) then optional STT junk (spaces /
    # commas / periods STT often inserts), then a trigger word or punct char.
    # e.g. "top period", "top, period", "top, period,"
    magic_escaped = re.escape(magic_lower)
    sep = r"[\s.,:;!?]*"
    pattern = re.compile(
        rf"(?i)\b{magic_escaped}\b{sep}({alt_pattern})",
        re.UNICODE,
    )

    # Phase 1: Replace magic_word + trigger/punct with a placeholder.
    # Marker must NOT contain any AUTO_PUNCT_CHARS (those are stripped in phase 2).
    MARKER_PREFIX = "\x00P"
    MARKER_SUFFIX = "\x00"
    counter = 0
    protected: dict[str, str] = {}

    def replace_magic(m: re.Match) -> str:
        nonlocal counter
        matched_trigger = m.group(1).lower().rstrip()
        # Look up what punctuation this trigger maps to
        punct = trigger_map.get(matched_trigger) or punct_identity.get(matched_trigger, "")
        if not punct:
            return m.group(0)  # no match, leave as-is
        key = f"{MARKER_PREFIX}{counter}{MARKER_SUFFIX}"
        protected[key] = punct
        counter += 1
        return key

    text = pattern.sub(replace_magic, draft)

    # Phase 2: Strip all unprotected auto-punctuation characters
    result = []
    for ch in text:
        if ch in AUTO_PUNCT_CHARS:
            continue  # strip auto-generated punct
        result.append(ch)
    text = "".join(result)

    # Phase 3: Restore protected punctuation
    for key, punct in protected.items():
        text = text.replace(key, punct)

    # Phase 4: Clean up whitespace (collapse multiple spaces, trim around punct)
    text = re.sub(r"\s+", " ", text)  # collapse whitespace
    # Remove space before punctuation
    text = re.sub(r"\s+([.,?!:;)\]\"'])", r"\1", text)
    # Remove space after opening brackets/quotes
    text = re.sub(r"([\(\[\"])\s+", r"\1", text)
    text = text.strip()

    return text
