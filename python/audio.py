"""
Audio capture with voice activity detection (VAD) for chunk-based streaming.

Phase 1: simple energy (RMS) VAD — no extra dependencies.
Phase 3: drop-in webrtcvad replacement via the USE_WEBRTCVAD flag.

Chunks are emitted as WAV bytes via an async callback scheduled on the
provided asyncio event loop (thread-safe).
"""

from __future__ import annotations

import asyncio
import io
import threading
import wave
from collections.abc import Awaitable, Callable

import numpy as np
import sounddevice as sd

# Set True to use webrtcvad instead of energy VAD (requires: pip install webrtcvad)
USE_WEBRTCVAD = False

SAMPLE_RATE = 16_000  # Hz  — required by Voxtral
CHANNELS = 1
DTYPE = "int16"
BLOCK_MS = 30  # ms per audio frame fed to VAD
BLOCK_SAMPLES = SAMPLE_RATE * BLOCK_MS // 1000  # 480 samples


def _frames_to_wav(frames: list[np.ndarray]) -> bytes:
    """Concatenate int16 frames and encode as WAV bytes."""
    audio = np.concatenate(frames).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio.tobytes())
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Energy VAD (Phase 1 — default)
# ---------------------------------------------------------------------------


class _EnergyVAD:
    """
    Simple RMS-based voice activity detector.
    Suitable for quiet thesis-writing environments.
    """

    def __init__(self, rms_threshold: int = 400, silence_frames: int = 50):
        self.rms_threshold = rms_threshold
        self.silence_frames = silence_frames  # ~1.5 s at 30 ms/frame

    def is_speech(self, frame: np.ndarray) -> bool:
        rms = int(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))
        return rms > self.rms_threshold


# ---------------------------------------------------------------------------
# WebRTC VAD (Phase 3 — higher accuracy)
# ---------------------------------------------------------------------------


class _WebRTCVAD:
    def __init__(self, mode: int = 2):
        import webrtcvad  # noqa: PLC0415

        self._vad = webrtcvad.Vad(mode)

    def is_speech(self, frame: np.ndarray) -> bool:
        pcm_bytes = frame.astype(np.int16).tobytes()
        try:
            return self._vad.is_speech(pcm_bytes, SAMPLE_RATE)
        except Exception:
            return False


# ---------------------------------------------------------------------------
# AudioCapture
# ---------------------------------------------------------------------------


class AudioCapture:
    """
    Continuously records microphone audio and emits speech chunks via callback.

    Usage:
        capture = AudioCapture(on_chunk=my_async_fn, loop=asyncio_loop)
        capture.start()
        # ... user speaks ...
        capture.stop()   # also flushes any remaining speech

    Callback:
        async def on_chunk(wav_bytes: bytes) -> None: ...
    """

    def __init__(
        self,
        on_chunk: Callable[[bytes], Awaitable[None]],
        loop: asyncio.AbstractEventLoop,
        vad_silence_duration: float = 1.5,
        vad_mode: int = 2,
        rms_threshold: int = 400,
    ):
        self._on_chunk = on_chunk
        self._loop = loop

        # Choose VAD backend
        if USE_WEBRTCVAD:
            self._vad = _WebRTCVAD(mode=vad_mode)
        else:
            silence_frames = max(1, int(vad_silence_duration / (BLOCK_MS / 1000)))
            self._vad = _EnergyVAD(
                rms_threshold=rms_threshold,
                silence_frames=silence_frames,
            )

        self._silence_limit = max(1, int(vad_silence_duration / (BLOCK_MS / 1000)))
        self._min_speech_frames = 5  # ~150 ms minimum speech

        self._speech_frames: list[np.ndarray] = []
        self._silence_count: int = 0
        self._stream: sd.InputStream | None = None
        self._recording = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self):
        with self._lock:
            if self._recording:
                return
            self._recording = True
            self._speech_frames = []
            self._silence_count = 0
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype=DTYPE,
                blocksize=BLOCK_SAMPLES,
                callback=self._audio_callback,
            )
            self._stream.start()

    def stop(self):
        with self._lock:
            if not self._recording:
                return
            self._recording = False
            if self._stream:
                self._stream.stop()
                self._stream.close()
                self._stream = None
            # Flush remaining speech
            if len(self._speech_frames) >= self._min_speech_frames:
                self._emit(self._speech_frames.copy())
            self._speech_frames = []
            self._silence_count = 0

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info,
        status,
    ):
        if not self._recording:
            return
        frame = indata[:, 0]  # mono

        if self._vad.is_speech(frame):
            self._speech_frames.append(frame.copy())
            self._silence_count = 0
        else:
            if self._speech_frames:
                # Keep a few silence frames for natural trailing audio
                self._speech_frames.append(frame.copy())
                self._silence_count += 1

                if self._silence_count >= self._silence_limit:
                    if len(self._speech_frames) >= self._min_speech_frames:
                        self._emit(self._speech_frames.copy())
                    self._speech_frames = []
                    self._silence_count = 0

    def _emit(self, frames: list[np.ndarray]):
        """Convert frames to WAV and schedule the async callback on the event loop."""
        try:
            wav_bytes = _frames_to_wav(frames)
        except Exception as e:
            print(f"[audio] WAV conversion error: {e}")
            return

        asyncio.run_coroutine_threadsafe(
            self._on_chunk(wav_bytes),
            self._loop,
        )
