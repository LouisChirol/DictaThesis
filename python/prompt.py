"""
System prompt assembly and voice command → text mapping for the 2nd-pass LLM.

Command definitions are loaded from settings (dictation_commands) and used to
dynamically build the JSON schema enum and prompt section.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Dynamic JSON schema builder
# ---------------------------------------------------------------------------


def build_response_schema(command_ids: list[str]) -> dict:
    """Build the JSON response schema with the given command IDs in the enum."""
    all_ids = ["none"] + [cid for cid in command_ids if cid != "none"]
    return {
        "type": "object",
        "properties": {
            "segments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": ["text", "command"]},
                        "content": {"type": "string"},
                        "command": {"type": "string", "enum": all_ids},
                    },
                    "required": ["type", "content", "command"],
                },
            },
            "full_text": {"type": "string"},
            "detected_language": {"type": "string", "enum": ["fr", "en"]},
        },
        "required": ["segments", "full_text", "detected_language"],
    }


# ---------------------------------------------------------------------------
# Command prompt section builder
# ---------------------------------------------------------------------------


def build_command_prompt_section(commands: list[dict]) -> str:
    """Generate the voice commands block for the system prompt from command definitions."""
    formatting_lines: list[str] = []
    editing_lines: list[str] = []
    control_lines: list[str] = []

    for cmd in commands:
        triggers = " / ".join(f'"{t}"' for t in cmd["triggers"])
        cmd_id = cmd["id"]
        desc = cmd.get("description", "")
        category = cmd.get("category", "formatting")

        extra = ""
        if cmd_id == "bibliography_ref":
            extra = " (content = the number N as a string)"
        if desc:
            extra += f" — {desc}"

        line = f"- {triggers}  → command: {cmd_id}{extra}"

        if category == "editing":
            editing_lines.append(line)
        elif category == "control":
            control_lines.append(line)
        else:
            formatting_lines.append(line)

    sections = []
    if formatting_lines:
        sections.append("### Formatting commands (applied as text in `full_text`):\n"
                        + "\n".join(formatting_lines))
    if editing_lines:
        sections.append("### Editing commands (produce NO text in `full_text`, only in `segments`):\n"
                        + "\n".join(editing_lines))
    if control_lines:
        sections.append("### Control commands (produce NO text in `full_text`):\n"
                        + "\n".join(control_lines))

    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_NORMAL = """\
You are DictaThesis, a smart dictation assistant for academic thesis writing. \
You process raw speech-to-text transcriptions and produce refined, ready-to-insert text.

## Your tasks
1. Fix transcription errors (misheard words, homophones) using the provided vocabulary.
2. Capitalize the first word of each sentence. Do NOT add punctuation that the user did not \
dictate. The user controls punctuation explicitly via voice commands.
3. Detect voice commands and separate them from dictated text.
4. Maintain a formal academic register appropriate for a doctoral thesis.
5. Use the bibliography context to correctly resolve citation reference numbers.

## CRITICAL: Punctuation rules
- Do NOT add periods, commas, or any punctuation at the end of the text unless the user \
explicitly dictated a punctuation command (e.g., "commande point").
- The STT model often auto-adds a trailing period to its output — you MUST remove it \
unless the user explicitly said a punctuation command. Strip any auto-generated trailing punctuation.
- The user dictates punctuation explicitly using voice commands prefixed with the magic word.
- Only fix obvious transcription errors in existing punctuation, never add new punctuation.

## Voice command detection
Commands are triggered by a **magic prefix word**: the user says "commande" (French) or \
"command" (English) followed by the command trigger phrase. Without this prefix, the words \
must be treated as regular dictated text.

Examples:
- "commande point" → period command (inserts ".")
- "le point principal" → regular text, NOT a command (no prefix)
- "command new line" → newline command (inserts "\\n")
- "commande supprimer la phrase précédente" → delete_previous_sentence command
- "commande titre un" → heading1 command

The magic prefix may be slightly misspelled by STT (e.g., "command", "commandes", "comandé"). \
Be tolerant of minor variations.

{commands}

## Output rules
- Return a JSON object matching the schema exactly.
- `full_text`: the complete ready-to-insert text with **formatting** commands already applied as \
plain-text formatting. For example, period becomes ".", newline becomes "\\n", \
heading1 prepends "# " (Markdown), bibliography_ref for N=3 becomes "\\cite{{ref3}}".
- **Editing and control commands** (e.g., delete_previous_sentence, stop_dictation) must appear \
ONLY in `segments` — they produce NO text in `full_text`. If the entire utterance is an \
editing/control command, `full_text` must be an empty string "".
- `segments`: the raw breakdown showing which parts are text vs commands. \
Split text and commands into separate segment objects.
- `detected_language`: the language of the dictation ("fr" or "en").
- Voice commands must be recognized even if the STT slightly misspells the trigger. \
For example, "supprimer la phrase précédente" or "supprimer la phrase precedente" \
should both match delete_previous_sentence.
- **Continuity**: Your output will be appended directly after the tail text shown in context. \
Ensure proper spacing and punctuation continuity. Do NOT repeat the tail text.
- **Never add a trailing period or punctuation** unless the user explicitly dictated one via a command.

{context}
"""

SYSTEM_PROMPT_EQUATION = """\
You are DictaThesis in equation mode. Convert spoken mathematics into valid LaTeX equations.
Both French and English mathematical speech are accepted.

## Conversion examples
- "x carré" / "x squared"                    → x^2
- "x au cube"                                 → x^3
- "racine de x" / "square root of x"          → \\sqrt{x}
- "fraction a sur b" / "a over b"             → \\frac{a}{b}
- "intégrale de a à b de f de x dx"           → \\int_a^b f(x) \\, dx
- "intégrale de zéro à l'infini"              → \\int_0^{\\infty}
- "somme de i égale un à n"                   → \\sum_{i=1}^{n}
- "x indice i" / "x sub i"                    → x_i
- "x exposant n" / "x to the n"               → x^n
- "infini" / "infinity"                        → \\infty
- "alpha, bêta, gamma, delta, lambda, mu"     → \\alpha, \\beta, \\gamma, \\delta, \\lambda, \\mu
- "égale" / "equals"                          → =
- "plus ou moins" / "plus or minus"           → \\pm
- "appartient à" / "in" / "element of"        → \\in
- "pour tout" / "for all"                     → \\forall
- "il existe" / "there exists"                → \\exists

## Output rules
Return a JSON object with:
- `full_text`: the LaTeX string only (no surrounding $$ or \\[ \\], just the content)
- `segments`: a single text segment containing the LaTeX
- `detected_language`: "fr" or "en"
"""


def build_prompt(
    draft_text: str,
    session_context: list[str],
    settings,
    mode: str = "normal",
    injected_tail: str = "",
) -> tuple[str, str]:
    """
    Build (system_prompt, user_message) for the 2nd-pass LLM call.

    Returns:
        (system_prompt, user_message)
    """
    if mode == "equation":
        system = SYSTEM_PROMPT_EQUATION
        user = f'Convert this spoken math to LaTeX:\n"{draft_text}"'
        return system, user

    # Build commands section from settings
    commands = settings.get("dictation_commands") or []
    commands_section = build_command_prompt_section(commands) if commands else "(no commands defined)"

    # Build dynamic context block
    context_parts: list[str] = []

    if injected_tail:
        context_parts.append(
            f"### Tail of text already in document (continue seamlessly, do NOT repeat):\n"
            f"...{injected_tail}"
        )

    if session_context:
        recent = " ".join(session_context[-5:])
        context_parts.append(f"### Recent dictated text (for coherence and context):\n{recent}")

    vocabulary = settings.get("vocabulary")
    if vocabulary:
        terms = ", ".join(vocabulary)
        context_parts.append(
            f"### Technical vocabulary — use these exact spellings when correcting:\n{terms}"
        )

    bibliography = settings.get("bibliography")
    if bibliography:
        context_parts.append(
            f"### Bibliography — use for resolving reference numbers:\n{bibliography}"
        )

    lang = settings.get("language")
    context_parts.append(f"### Expected language: {lang}")

    context_block = "\n\n".join(context_parts) if context_parts else "(no additional context)"
    system = SYSTEM_PROMPT_NORMAL.replace("{commands}", commands_section)
    system = system.replace("{context}", context_block)

    user = f'Raw transcription to refine:\n"{draft_text}"'
    return system, user


# ---------------------------------------------------------------------------
# Command → text application (fallback when full_text is missing)
# ---------------------------------------------------------------------------


def apply_commands(
    segments: list[dict],
    commands: list[dict] | None = None,
) -> str:
    """
    Walk segments and produce the final text string, applying commands.
    This is a fallback if the LLM's `full_text` field is missing or empty.
    """
    # Build lookup from command definitions
    cmd_lookup: dict[str, dict] = {}
    if commands:
        for cmd in commands:
            cmd_lookup[cmd["id"]] = cmd

    parts: list[str] = []
    for seg in segments:
        typ = seg.get("type", "text")
        content = seg.get("content", "")
        command = seg.get("command", "none")

        if typ == "text":
            parts.append(content)
        elif typ == "command":
            parts.append(_command_to_text(command, content, cmd_lookup))

    return "".join(parts)


def _command_to_text(command: str, content: str, cmd_lookup: dict[str, dict]) -> str:
    if command == "none":
        return content

    # Look up command definition
    cmd_def = cmd_lookup.get(command)
    if not cmd_def:
        return content

    action = cmd_def.get("action", {})
    action_type = action.get("type", "")

    if action_type == "insert_text":
        text = action.get("text", content)
        # Handle bibliography_ref placeholder
        if "__N__" in text:
            text = text.replace("__N__", content)
        return text
    elif action_type == "control":
        return ""  # control commands produce no text
    elif action_type == "llm_instruction":
        return content  # LLM should have already applied the instruction in full_text
    elif action_type == "edit":
        return ""  # editing commands are handled by the pipeline, not text substitution

    return content
