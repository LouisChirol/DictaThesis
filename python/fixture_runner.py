"""
Run DictaThesis pipeline stages against test/recording fixtures.

Usage (from python/):
  uv run python fixture_runner.py              # chunked STT + batched refine
  uv run python fixture_runner.py --stt-only   # pass 1 only (per chunk)
  uv run python fixture_runner.py --full-stt    # pass 1 on entire file
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import re
import subprocess
import sys
import wave
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

import api_client
from audio import BLOCK_MS, BLOCK_SAMPLES, SAMPLE_RATE, _EnergyVAD, _frames_to_wav
from pipeline import REFINE_BATCH_DEBOUNCE_S, REFINE_BATCH_MAX_CHUNKS, REFINE_TIMEOUT_S
from settings_store import SettingsStore

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "test"
AUDIO_M4A = FIXTURE_DIR / "recording.m4a"
AUDIO_WAV = FIXTURE_DIR / "recording.wav"
GROUND_TRUTH = FIXTURE_DIR / "ground_truth.md"
VERBATIM = FIXTURE_DIR / "verbatim.md"
RESULTS_DIR = FIXTURE_DIR / "results"

# Domain terms from the paper intro (context_bias experiment)
FIXTURE_VOCABULARY = [
    "plasmides",
    "stochastique",
    "individu",
    "centré",
    "séquence",
    "type",
    "intra-hôte",
    "inter-hôte",
    "résistance",
    "chromosome",
    "bactériennes",
    "hospitalier",
]


def ensure_wav() -> Path:
    if AUDIO_WAV.exists() and AUDIO_WAV.stat().st_mtime >= AUDIO_M4A.stat().st_mtime:
        return AUDIO_WAV
    print(f"[fixture] Converting {AUDIO_M4A.name} → WAV 16 kHz mono…")
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(AUDIO_M4A),
            "-ar",
            str(SAMPLE_RATE),
            "-ac",
            "1",
            str(AUDIO_WAV),
        ],
        check=True,
        capture_output=True,
    )
    return AUDIO_WAV


def read_markdown_body(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    # Drop markdown headings for comparison (fixture uses # / ## as structure)
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith("#")]
    return "\n".join(lines).strip()


def normalize_for_compare(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def similarity(a: str, b: str) -> float:
    na, nb = normalize_for_compare(a), normalize_for_compare(b)
    if not na and not nb:
        return 1.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def word_diff_summary(got: str, expected: str, label: str) -> str:
    nw = normalize_for_compare(got).split()
    ew = normalize_for_compare(expected).split()
    sm = difflib.SequenceMatcher(None, ew, nw)
    adds, dels = 0, 0
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "replace":
            dels += i2 - i1
            adds += j2 - j1
        elif op == "delete":
            dels += i2 - i1
        elif op == "insert":
            adds += j2 - j1
    ratio = similarity(got, expected)
    return f"{label}: similarity={ratio:.1%}  missing_words~{dels}  extra_words~{adds}"


def load_audio_frames(wav_path: Path) -> list[np.ndarray]:
    with wave.open(str(wav_path), "rb") as wf:
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
            raise ValueError("Expected 16-bit mono WAV")
        samples = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    frames: list[np.ndarray] = []
    for start in range(0, len(samples) - BLOCK_SAMPLES + 1, BLOCK_SAMPLES):
        frames.append(samples[start : start + BLOCK_SAMPLES])
    return frames


def chunk_wav_frames(
    frames: list[np.ndarray],
    vad_silence_duration: float = 1.5,
    max_chunk_duration: float = 6.0,
    rms_threshold: int = 400,
) -> list[bytes]:
    """Replay audio through energy VAD — same chunking rules as live capture."""
    silence_limit = max(1, int(vad_silence_duration / (BLOCK_MS / 1000)))
    max_chunk_frames = max(1, int(max_chunk_duration / (BLOCK_MS / 1000)))
    min_speech_frames = 6
    vad = _EnergyVAD(rms_threshold=rms_threshold, silence_frames=silence_limit)

    chunks: list[bytes] = []
    speech_frames: list[np.ndarray] = []
    voiced_frames = 0
    chunk_frame_count = 0
    silence_count = 0

    def emit():
        nonlocal speech_frames, voiced_frames, chunk_frame_count, silence_count
        if voiced_frames >= min_speech_frames and speech_frames:
            chunks.append(_frames_to_wav(speech_frames))
        speech_frames = []
        voiced_frames = 0
        chunk_frame_count = 0
        silence_count = 0

    for frame in frames:
        is_speech = vad.is_speech(frame)
        if is_speech:
            speech_frames.append(frame.copy())
            voiced_frames += 1
            chunk_frame_count += 1
            silence_count = 0
        else:
            if speech_frames:
                speech_frames.append(frame.copy())
                silence_count += 1
                chunk_frame_count += 1
                if silence_count >= silence_limit:
                    emit()
        if speech_frames and chunk_frame_count >= max_chunk_frames:
            emit()

    if speech_frames:
        emit()

    return chunks


async def transcribe_chunks(
    wav_chunks: list[bytes],
    api_key: str,
    language: str,
    vocabulary: list[str],
) -> list[str]:
    drafts: list[str] = []
    for i, wav in enumerate(wav_chunks):
        draft = (await api_client.transcribe(
            wav,
            api_key,
            language,
            vocabulary=vocabulary,
        )).strip()
        drafts.append(draft)
        if not draft:
            continue
        dur = len(wav) / (SAMPLE_RATE * 2)  # rough
        print(f"[fixture] STT chunk {i}: {draft[:72]!r}… ({dur:.1f}s audio)")
    return drafts


async def refine_batched(
    drafts: list[str],
    api_key: str,
    settings: SettingsStore,
) -> str:
    """Mirror pipeline batch refine (up to 4 consecutive chunks)."""
    parts: list[str] = []
    i = 0
    while i < len(drafts):
        batch = drafts[i : i + REFINE_BATCH_MAX_CHUNKS]
        combined = " ".join(t.strip() for t in batch if t.strip())
        if not combined:
            i += len(batch)
            continue

        print(
            f"[fixture] Refining batch chunks {i}-{i + len(batch) - 1}: "
            f"{combined[:72]!r}…"
        )
        try:
            result = await asyncio.wait_for(
                api_client.refine(
                    combined,
                    api_key,
                    [],
                    settings,
                    settings.get("mode"),
                    injected_tail="",
                    open_delimiters=[],
                ),
                timeout=REFINE_TIMEOUT_S,
            )
            text = (result.get("full_text") or combined).strip()
        except (TimeoutError, Exception) as e:
            print(f"[fixture] Refine fallback ({e}): using draft")
            text = combined

        parts.append(text)
        i += len(batch)
        if i < len(drafts) and REFINE_BATCH_DEBOUNCE_S > 0:
            await asyncio.sleep(REFINE_BATCH_DEBOUNCE_S)

    return "\n\n".join(parts)


async def run_stt_only(settings: SettingsStore, vocabulary: list[str]) -> dict:
    wav_path = ensure_wav()
    frames = load_audio_frames(wav_path)
    wav_chunks = chunk_wav_frames(
        frames,
        vad_silence_duration=settings.get("vad_silence_duration"),
        max_chunk_duration=settings.get("max_chunk_duration"),
    )
    print(f"[fixture] VAD produced {len(wav_chunks)} chunks")

    api_key = settings.get("api_key")
    if not api_key:
        sys.exit("No api_key in settings — configure Mistral key in the app first.")

    lang = settings.get("language") or "fr"
    if lang == "auto":
        lang = "fr"

    drafts = await transcribe_chunks(wav_chunks, api_key, lang, vocabulary)
    combined = " ".join(drafts)
    return {
        "mode": "stt_only",
        "chunk_count": len(wav_chunks),
        "drafts": drafts,
        "combined_draft": combined,
    }


async def run_pipeline(settings: SettingsStore, vocabulary: list[str]) -> dict:
    stt = await run_stt_only(settings, vocabulary)
    api_key = settings.get("api_key")
    refined = await refine_batched(stt["drafts"], api_key, settings)
    stt["mode"] = "pipeline"
    stt["refined"] = refined
    return stt


async def run_full_stt(settings: SettingsStore, vocabulary: list[str]) -> dict:
    wav_path = ensure_wav()
    wav_bytes = wav_path.read_bytes()
    api_key = settings.get("api_key")
    if not api_key:
        sys.exit("No api_key in settings")
    lang = settings.get("language") or "fr"
    if lang == "auto":
        lang = "fr"
    print("[fixture] Full-file STT (single request)…")
    text = await api_client.transcribe(wav_bytes, api_key, lang, vocabulary=vocabulary)
    return {"mode": "full_stt", "text": text}


def print_report(result: dict, verbatim_text: str, ground_truth_text: str):
    print("\n" + "=" * 60)
    if result.get("combined_draft"):
        print(word_diff_summary(result["combined_draft"], verbatim_text, "STT vs verbatim"))
    if result.get("text"):
        print(word_diff_summary(result["text"], verbatim_text, "Full STT vs verbatim"))
    if result.get("refined"):
        print(word_diff_summary(result["refined"], ground_truth_text, "Refined vs ground_truth"))
    print("=" * 60)

    if result.get("drafts"):
        print("\n--- Per-chunk drafts ---")
        for i, d in enumerate(result["drafts"]):
            print(f"  [{i}] {d}")

    if result.get("refined"):
        print("\n--- Refined output ---")
        print(result["refined"])

    if result.get("text"):
        print("\n--- Full STT ---")
        print(result["text"])


async def main():
    parser = argparse.ArgumentParser(description="Run DictaThesis fixture tests")
    parser.add_argument("--stt-only", action="store_true", help="Pass 1 only (chunked)")
    parser.add_argument("--full-stt", action="store_true", help="Pass 1 on entire WAV")
    parser.add_argument("--no-vocab", action="store_true", help="Skip context_bias vocabulary")
    args = parser.parse_args()

    settings = SettingsStore()
    vocabulary = [] if args.no_vocab else FIXTURE_VOCABULARY
    verbatim_text = read_markdown_body(VERBATIM) if VERBATIM.exists() else ""
    ground_truth_text = read_markdown_body(GROUND_TRUTH) if GROUND_TRUTH.exists() else ""

    if args.full_stt:
        result = await run_full_stt(settings, vocabulary)
    elif args.stt_only:
        result = await run_stt_only(settings, vocabulary)
    else:
        result = await run_pipeline(settings, vocabulary)

    print_report(result, verbatim_text, ground_truth_text)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    out = RESULTS_DIR / f"{stamp}_{result['mode']}.json"
    payload = {
        **result,
        "verbatim_similarity": similarity(result.get("combined_draft") or result.get("text") or "", verbatim_text),
        "ground_truth_similarity": similarity(result.get("refined") or "", ground_truth_text),
        "vocabulary": vocabulary,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[fixture] Results saved to {out}")


if __name__ == "__main__":
    asyncio.run(main())
