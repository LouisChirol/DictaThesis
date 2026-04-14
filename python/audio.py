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
import os
import threading
import urllib.request
import wave
from collections.abc import Awaitable, Callable
from pathlib import Path

import numpy as np
import sounddevice as sd

# Set True to use webrtcvad instead of energy VAD (requires: pip install webrtcvad)
USE_WEBRTCVAD = False

SAMPLE_RATE = 16_000  # Hz  — required by Voxtral
CHANNELS = 1
DTYPE = "int16"
BLOCK_MS = 30  # ms per audio frame fed to VAD
BLOCK_SAMPLES = SAMPLE_RATE * BLOCK_MS // 1000  # 480 samples
SILERO_ONNX_URL = (
    "https://raw.githubusercontent.com/snakers4/silero-vad/master/src/silero_vad/data/silero_vad.onnx"
)


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


class _SileroVAD:
    def __init__(
        self,
        threshold: float = 0.6,
        model_path: str | None = None,
    ):
        import onnxruntime as ort  # noqa: PLC0415

        self._threshold = threshold
        self._session = ort.InferenceSession(str(self._ensure_model(model_path)))
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, 64), dtype=np.float32)

    def _ensure_model(self, model_path: str | None) -> Path:
        if model_path:
            path = Path(model_path).expanduser()
            if path.exists():
                return path
            raise FileNotFoundError(f"Silero model not found: {path}")

        cache_dir = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "dictathesis"
        cache_dir.mkdir(parents=True, exist_ok=True)
        target = cache_dir / "silero_vad.onnx"
        if not target.exists():
            urllib.request.urlretrieve(SILERO_ONNX_URL, target)  # noqa: S310
        return target

    def is_speech(self, frame: np.ndarray) -> bool:
        x = frame.astype(np.float32).reshape(1, -1) / 32768.0
        if x.shape[1] != BLOCK_SAMPLES:
            return False
        x_in = np.concatenate([self._context, x], axis=1)
        self._context = x[:, -64:].copy()
        inputs = {
            "input": x_in.astype(np.float32),
            "state": self._state,
            "sr": np.array([SAMPLE_RATE], dtype=np.int64),
        }
        out, state = self._session.run(None, inputs)
        self._state = state
        prob = float(np.squeeze(out))
        return prob >= self._threshold


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
        max_chunk_duration: float = 12.0,
        vad_backend: str = "energy",
        vad_mode: int = 2,
        rms_threshold: int = 400,
    ):
        self._on_chunk = on_chunk
        self._loop = loop

        # Choose VAD backend
        backend = (vad_backend or "energy").lower()
        if backend == "silero":
            try:
                self._vad = _SileroVAD()
                print("[audio] Using Silero ONNX VAD backend")
            except Exception as e:
                print(f"[audio] Silero backend unavailable ({e}); trying WebRTC fallback")
                try:
                    self._vad = _WebRTCVAD(mode=vad_mode)
                    print("[audio] Using WebRTC VAD fallback")
                except Exception as we:
                    print(f"[audio] WebRTC fallback unavailable ({we}); using Energy VAD")
                    silence_frames = max(1, int(vad_silence_duration / (BLOCK_MS / 1000)))
                    self._vad = _EnergyVAD(
                        rms_threshold=rms_threshold,
                        silence_frames=silence_frames,
                    )
        elif backend == "webrtc" or USE_WEBRTCVAD:
            try:
                self._vad = _WebRTCVAD(mode=vad_mode)
                print("[audio] Using WebRTC VAD backend")
            except Exception as e:
                print(f"[audio] WebRTC backend unavailable ({e}); using Energy VAD")
                silence_frames = max(1, int(vad_silence_duration / (BLOCK_MS / 1000)))
                self._vad = _EnergyVAD(
                    rms_threshold=rms_threshold,
                    silence_frames=silence_frames,
                )
        else:
            silence_frames = max(1, int(vad_silence_duration / (BLOCK_MS / 1000)))
            self._vad = _EnergyVAD(
                rms_threshold=rms_threshold,
                silence_frames=silence_frames,
            )

        self._silence_limit = max(1, int(vad_silence_duration / (BLOCK_MS / 1000)))
        self._max_chunk_frames = max(1, int(max_chunk_duration / (BLOCK_MS / 1000)))
        self._min_speech_frames = 10  # ~300 ms of voiced audio required

        self._speech_frames: list[np.ndarray] = []
        self._voiced_frames: int = 0
        self._chunk_frame_count: int = 0
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
            self._voiced_frames = 0
            self._chunk_frame_count = 0
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
            if self._voiced_frames >= self._min_speech_frames:
                self._emit(self._speech_frames.copy())
            self._speech_frames = []
            self._voiced_frames = 0
            self._chunk_frame_count = 0
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
            self._voiced_frames += 1
            self._chunk_frame_count += 1
            self._silence_count = 0
        else:
            if self._speech_frames:
                # Keep a few silence frames for natural trailing audio
                self._speech_frames.append(frame.copy())
                self._silence_count += 1
                self._chunk_frame_count += 1

                if self._silence_count >= self._silence_limit:
                    if self._voiced_frames >= self._min_speech_frames:
                        self._emit(self._speech_frames.copy())
                    self._speech_frames = []
                    self._voiced_frames = 0
                    self._chunk_frame_count = 0
                    self._silence_count = 0

        # Hard chunk cut: emit regularly even during long continuous speech.
        if self._speech_frames and self._chunk_frame_count >= self._max_chunk_frames:
            if self._voiced_frames >= self._min_speech_frames:
                self._emit(self._speech_frames.copy())
            self._speech_frames = []
            self._voiced_frames = 0
            self._chunk_frame_count = 0
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
