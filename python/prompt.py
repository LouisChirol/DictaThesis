"""
System prompt assembly and voice command → text mapping for DictaThesis.

Pass 2: build_prompt — Mistral Medium thesis refinement + command detection.
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
            extra = " (content = the reference number N as a string)"
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
        sections.append(
            "### Structure and delimiter commands (MUST appear in `full_text` as the "
            "actual characters, including when the utterance is ONLY the command):\n"
            + "\n".join(formatting_lines)
            + "\n- A lone line-break command → `full_text` is a single newline (`\\n`), not empty."
            "\n- A lone paragraph command → `full_text` is two newlines (`\\n\\n`), not empty."
        )
    if editing_lines:
        sections.append(
            "### Editing commands (NO text in `full_text`, only in `segments`):\n"
            + "\n".join(editing_lines)
        )
    if control_lines:
        sections.append(
            "### Control commands (NO text in `full_text`):\n" + "\n".join(control_lines)
        )

    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Pass 2 — Mistral Large system prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_NORMAL = """\
You are DictaThesis, a smart dictation assistant for doctoral thesis writing.
You receive a raw speech-to-text draft and produce refined, ready-to-paste academic text.

## Core behavior
1. The user speaks their paper naturally — they do NOT say "point" or "virgule" for punctuation.
2. Add appropriate French or English academic punctuation: periods, commas, colons, semicolons, \
question and exclamation marks, with correct spacing.
3. Fix transcription errors using the vocabulary and bibliography context.
4. Capitalize sentence starts. Maintain a formal thesis register.
5. Detect voice commands (distinctive phrases below) and separate them from dictated prose.

## Smart punctuation and paired marks
- Infer sentence boundaries from syntax and pauses implied in the draft.
- For parentheses, quotes, and brackets:
  - If the user says "ouvrir parenthèse" / "open parenthesis" → insert "("
  - If they say "fermer parenthèse" / "close parenthesis" → insert ")"
  - French quotes: « and » (English: use straight double quotes when language is en)
  - Brackets: [ and ]
- If they say "entre parenthèses …" or "je cite …", wrap the following clause in parentheses or quotes.
- If open delimiters are listed in context (unclosed «, (, [), close them when the clause ends \
or leave open if the chunk is clearly incomplete.
- Do NOT wrap every "par exemple" in parentheses. When ambiguous, prefer no pair over a wrong pair.
- Words like "point de vue", "à ce point", "le point principal" are CONTENT, not commands.

## Voice commands (no prefix word required)
Recognize these phrases even if STT slightly misspells them. \
"le point principal" is text; "nouveau paragraphe" / "à la ligne" are commands.

{commands}

## Output rules
- Return JSON matching the schema exactly.
- `full_text`: complete ready-to-insert text with formatting commands already applied \
(newlines, headings, \\cite{{refN}}, delimiters, $ for equations).
- Structure commands are NOT empty: even a command-only utterance must put the inserted \
characters in `full_text` (one newline for line break, two for a new paragraph).
- Editing and control commands appear ONLY in `segments` with empty `full_text` if the \
utterance is purely a command.
- `segments`: breakdown of text vs commands.
- `detected_language`: "fr" or "en".
- **Continuity**: output is appended after the document tail in context. \
Do NOT repeat the tail. Match spacing and punctuation continuity.

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
    open_delimiters: list[str] | None = None,
) -> tuple[str, str]:
    """
    Build (system_prompt, user_message) for the 2nd-pass LLM call.
    """
    if mode == "equation":
        system = SYSTEM_PROMPT_EQUATION
        user = f'Convert this spoken math to LaTeX:\n"{draft_text}"'
        return system, user

    commands = settings.get("dictation_commands") or []
    commands_section = (
        build_command_prompt_section(commands) if commands else "(no commands defined)"
    )

    context_parts: list[str] = []

    if injected_tail:
        context_parts.append(
            f"### Tail of text already in document (continue seamlessly, do NOT repeat):\n"
            f"...{injected_tail}"
        )

    if open_delimiters:
        labels = ", ".join(open_delimiters)
        context_parts.append(
            f"### Unclosed delimiters in the session so far: {labels}\n"
            "Close them when appropriate or leave open if this chunk is incomplete."
        )

    if session_context:
        recent = " ".join(session_context[-5:])
        context_parts.append(f"### Recent dictated text (for coherence):\n{recent}")

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

    user = f'Raw transcription draft to refine:\n"{draft_text}"'
    return system, user


# ---------------------------------------------------------------------------
# Command → text application (fallback when full_text is missing)
# ---------------------------------------------------------------------------


def apply_commands(
    segments: list[dict],
    commands: list[dict] | None = None,
) -> str:
    """Walk segments and produce the final text string, applying commands."""
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

    cmd_def = cmd_lookup.get(command)
    if not cmd_def:
        return content

    action = cmd_def.get("action", {})
    action_type = action.get("type", "")

    if action_type == "insert_text":
        text = action.get("text", content)
        if "__N__" in text:
            text = text.replace("__N__", content)
        return text
    elif action_type == "control":
        return ""
    elif action_type == "llm_instruction":
        return content
    elif action_type == "edit":
        return ""

    return content
