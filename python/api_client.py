"""
Async Mistral API client.
  - 1st pass: POST /v1/audio/transcriptions  (Voxtral Mini Transcribe 2)
  - 2nd pass: POST /v1/chat/completions      (Mistral Small, JSON output)
  - Cool path: paragraph polish              (Mistral Medium)
"""

from __future__ import annotations

import io
import json

import httpx

from prompt import POLISH_RESPONSE_SCHEMA, build_polish_prompt, build_prompt, build_response_schema

BASE_URL = "https://api.mistral.ai/v1"
TRANSCRIPTION_MODEL = "voxtral-mini-latest"
REFINEMENT_MODEL = "mistral-small-latest"
POLISH_MODEL = "mistral-medium-latest"
TIMEOUT = httpx.Timeout(120.0, connect=10.0)
MAX_CONTEXT_BIAS_TERMS = 100


class MistralAPIError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"Mistral API error {status}: {body}")
        self.status = status
        self.body = body


async def transcribe(
    wav_bytes: bytes,
    api_key: str,
    language: str = "fr",
    injected_tail: str = "",
    vocabulary: list[str] | None = None,
) -> str:
    """
    Pass 1: dedicated transcription endpoint (Voxtral Mini Transcribe 2).

    Uses context_bias from vocabulary (up to 100 terms). Document tail is not
    sent to STT — continuity is handled in pass 2 via injected_tail.
    """
    del injected_tail  # STT endpoint has no tail prompt; pass 2 uses it

    multipart: list[tuple[str, tuple[str | None, str | io.BytesIO, str | None]]] = [
        ("file", ("audio.wav", io.BytesIO(wav_bytes), "audio/wav")),
        ("model", (None, TRANSCRIPTION_MODEL)),
    ]
    if language and language != "auto":
        multipart.append(("language", (None, language)))

    bias: list[str] = []
    for term in (vocabulary or [])[:MAX_CONTEXT_BIAS_TERMS]:
        for part in str(term).split():
            cleaned = part.strip().strip(",")
            if cleaned:
                bias.append(cleaned)
    bias = bias[:MAX_CONTEXT_BIAS_TERMS]
    for term in bias:
        multipart.append(("context_bias", (None, term)))

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(
            f"{BASE_URL}/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            files=multipart,
        )
        if resp.status_code != 200:
            raise MistralAPIError(resp.status_code, resp.text)
        return resp.json().get("text", "").strip()


async def refine(
    draft_text: str,
    api_key: str,
    session_context: list[str],
    settings,
    mode: str = "normal",
    injected_tail: str = "",
    open_delimiters: list[str] | None = None,
) -> dict:
    """
    Pass 2: thesis-style refinement, smart punctuation, and command detection (Small).
    """
    system_prompt, user_message = build_prompt(
        draft_text,
        session_context,
        settings,
        mode,
        injected_tail=injected_tail,
        open_delimiters=open_delimiters,
    )

    commands = settings.get("dictation_commands") or []
    command_ids = [cmd["id"] for cmd in commands]
    response_schema = build_response_schema(command_ids)

    payload = {
        "model": REFINEMENT_MODEL,
        "temperature": 0.15,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "DictaThesisOutput",
                "strict": True,
                "schema": response_schema,
            },
        },
    }

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(
            f"{BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        if resp.status_code != 200:
            print(f"[api_client] Refinement error {resp.status_code}: {resp.text[:200]}")
            return _fallback(draft_text)

        body = resp.json()
        raw_content = body["choices"][0]["message"]["content"]
        try:
            parsed = json.loads(raw_content)
            if "full_text" not in parsed:
                return _fallback(draft_text)
            return parsed
        except (json.JSONDecodeError, KeyError):
            return _fallback(draft_text)


async def polish_paragraph(
    paragraph: str,
    api_key: str,
    settings,
    preceding_tail: str = "",
) -> dict | None:
    """
    Cool path: rewrite one owned paragraph with Medium.
    Returns parsed JSON {rewritten, changed} or None on failure.
    """
    system_prompt, user_message = build_polish_prompt(
        paragraph, settings, preceding_tail=preceding_tail
    )
    payload = {
        "model": POLISH_MODEL,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "DictaThesisPolish",
                "strict": True,
                "schema": POLISH_RESPONSE_SCHEMA,
            },
        },
    }

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(
            f"{BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        if resp.status_code != 200:
            print(f"[api_client] Polish error {resp.status_code}: {resp.text[:200]}")
            return None

        body = resp.json()
        raw_content = body["choices"][0]["message"]["content"]
        try:
            parsed = json.loads(raw_content)
            if "rewritten" not in parsed:
                return None
            return parsed
        except (json.JSONDecodeError, KeyError):
            return None


def _fallback(draft_text: str) -> dict:
    """Return a minimal valid result using the raw draft when LLM fails."""
    return {
        "segments": [{"type": "text", "content": draft_text, "command": "none"}],
        "full_text": draft_text,
        "detected_language": "fr",
    }
