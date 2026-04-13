"""
System prompt assembly and voice command → text mapping for the 2nd-pass LLM.
"""
from __future__ import annotations
import json

# ---------------------------------------------------------------------------
# JSON schema for structured LLM output
# ---------------------------------------------------------------------------

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["text", "command"]},
                    "content": {"type": "string"},
                    "command": {
                        "type": "string",
                        "enum": [
                            "none", "period", "comma", "newline",
                            "new_paragraph", "heading1", "heading2", "heading3",
                            "bibliography_ref", "bold_start", "bold_end",
                            "italic_start", "italic_end",
                            "equation_start", "equation_end",
                            "stop_dictation",
                        ],
                    },
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
# System prompts
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_NORMAL = """\
You are DictaThesis, a smart dictation assistant for academic thesis writing. \
You process raw speech-to-text transcriptions and produce refined, ready-to-insert text.

## Your tasks
1. Fix transcription errors (misheard words, homophones) using the provided vocabulary.
2. Add proper punctuation and capitalize the first word of each sentence.
3. Detect voice commands (French or English) and separate them from dictated text.
4. Maintain a formal academic register appropriate for a doctoral thesis.
5. Use the bibliography context to correctly resolve citation reference numbers.

## Voice commands to detect (accept both French and English variants)
- "point" / "period" / "point final"          → command: period
- "virgule" / "comma"                          → command: comma
- "à la ligne" / "new line" / "next line"      → command: newline
- "nouveau paragraphe" / "new paragraph"       → command: new_paragraph
- "titre un" / "heading one" / "title one"     → command: heading1
- "titre deux" / "heading two" / "title two"   → command: heading2
- "titre trois" / "heading three"              → command: heading3
- "référence N" / "reference number N" / "cite N"  → command: bibliography_ref  (content = the number N as a string)
- "gras" / "bold" / "en gras"                  → command: bold_start or bold_end
- "italique" / "italic"                        → command: italic_start or italic_end
- "début équation" / "start equation"          → command: equation_start
- "fin équation" / "end equation"              → command: equation_end
- "arrêter la dictée" / "stop dictation"       → command: stop_dictation

## Output rules
- Return a JSON object matching the schema exactly.
- `full_text`: the complete ready-to-insert text with all commands already applied as \
plain-text formatting. For example, "period" becomes ".", "newline" becomes "\\n", \
"heading1" prepends "# " (Markdown), "bibliography_ref" for N=3 becomes "\\cite{ref3}".
- `segments`: the raw breakdown showing which parts are text vs commands. \
Split text and commands into separate segment objects.
- `detected_language`: the language of the dictation ("fr" or "en").
- If you are unsure whether something is a command, treat it as text.

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

    # Build dynamic context block
    context_parts: list[str] = []

    if session_context:
        recent = " ".join(session_context[-5:])
        context_parts.append(
            f"### Recent dictated text (for coherence and context):\n{recent}"
        )

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
    system = SYSTEM_PROMPT_NORMAL.replace("{context}", context_block)

    user = f'Raw transcription to refine:\n"{draft_text}"'
    return system, user


# ---------------------------------------------------------------------------
# Command → text application
# ---------------------------------------------------------------------------

def apply_commands(segments: list[dict], bibliography: str = "", mode: str = "normal") -> str:
    """
    Walk segments and produce the final text string, applying commands.
    This is a fallback if the LLM's `full_text` field is missing or empty.
    """
    parts: list[str] = []
    for seg in segments:
        typ = seg.get("type", "text")
        content = seg.get("content", "")
        command = seg.get("command", "none")

        if typ == "text":
            parts.append(content)
        elif typ == "command":
            parts.append(_command_to_text(command, content))

    return "".join(parts)


def _command_to_text(command: str, content: str) -> str:
    mapping = {
        "period": ".",
        "comma": ",",
        "newline": "\n",
        "new_paragraph": "\n\n",
        "heading1": "\n# ",
        "heading2": "\n## ",
        "heading3": "\n### ",
        "bold_start": "**",
        "bold_end": "**",
        "italic_start": "_",
        "italic_end": "_",
        "equation_start": "$",
        "equation_end": "$",
        "stop_dictation": "",
        "none": content,
    }
    if command == "bibliography_ref":
        # content = reference number as string
        return f"\\cite{{ref{content}}}"
    return mapping.get(command, content)
