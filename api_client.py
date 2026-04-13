"""
Async Mistral API client.
  - 1st pass: POST /v1/audio/transcriptions  (Voxtral)
  - 2nd pass: POST /v1/chat/completions      (Mistral Medium, JSON output)
"""
from __future__ import annotations
import json
import io

import httpx

from prompt import RESPONSE_SCHEMA, build_prompt

BASE_URL = "https://api.mistral.ai/v1"
TRANSCRIPTION_MODEL = "voxtral-mini-latest"
REFINEMENT_MODEL = "mistral-medium-latest"
TIMEOUT = httpx.Timeout(60.0, connect=10.0)


class MistralAPIError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"Mistral API error {status}: {body}")
        self.status = status
        self.body = body


async def transcribe(wav_bytes: bytes, api_key: str, language: str = "fr") -> str:
    """
    Send a WAV audio chunk to Voxtral and return the raw transcription text.

    Args:
        wav_bytes: Raw WAV file bytes.
        api_key:   Mistral API key.
        language:  ISO 639-1 language hint ("fr", "en"). Pass "auto" to omit.
    """
    files = {"file": ("audio.wav", io.BytesIO(wav_bytes), "audio/wav")}
    data = {"model": TRANSCRIPTION_MODEL}
    if language and language != "auto":
        data["language"] = language

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(
            f"{BASE_URL}/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            files=files,
            data=data,
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
) -> dict:
    """
    Send a draft transcription through the 2nd-pass LLM for smart refinement.

    Returns a dict with keys: segments, full_text, detected_language.
    On any error, returns a minimal dict using the raw draft text so the
    pipeline can fall back gracefully.
    """
    system_prompt, user_message = build_prompt(
        draft_text, session_context, settings, mode
    )

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
                "schema": RESPONSE_SCHEMA,
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
            # Non-fatal: fall back to raw draft
            print(f"[api_client] Refinement error {resp.status_code}: {resp.text[:200]}")
            return _fallback(draft_text)

        body = resp.json()
        raw_content = body["choices"][0]["message"]["content"]
        try:
            parsed = json.loads(raw_content)
            # Validate minimal expected shape
            if "full_text" not in parsed:
                return _fallback(draft_text)
            return parsed
        except (json.JSONDecodeError, KeyError):
            return _fallback(draft_text)


def _fallback(draft_text: str) -> dict:
    """Return a minimal valid result using the raw draft when LLM fails."""
    return {
        "segments": [{"type": "text", "content": draft_text, "command": "none"}],
        "full_text": draft_text,
        "detected_language": "fr",
    }
