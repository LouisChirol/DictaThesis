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
import platform
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
SILERO_FRAME_SAMPLES = 512  # Silero ONNX expects 512 samples at 16 kHz
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
        threshold: float | None = None,
        model_path: str | None = None,
    ):
        import onnxruntime as ort  # noqa: PLC0415

        default_threshold = 0.35 if platform.system() == "Windows" else 0.6
        self._threshold = threshold if threshold is not None else default_threshold

        available = ort.get_available_providers()
        providers = ["CPUExecutionProvider"] if "CPUExecutionProvider" in available else None
        self._session = (
            ort.InferenceSession(str(self._ensure_model(model_path)), providers=providers)
            if providers
            else ort.InferenceSession(str(self._ensure_model(model_path)))
        )
        self._reset_state()

    def _reset_state(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, 64), dtype=np.float32)

    def reset(self) -> None:
        self._reset_state()

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
        # Silero VAD ONNX model expects 512 samples at 16 kHz.
        if frame.shape[0] < SILERO_FRAME_SAMPLES:
            pad = SILERO_FRAME_SAMPLES - frame.shape[0]
            frame = np.pad(frame, (0, pad), mode="constant")
        elif frame.shape[0] > SILERO_FRAME_SAMPLES:
            frame = frame[-SILERO_FRAME_SAMPLES:]

        x = frame.astype(np.float32).reshape(1, -1) / 32768.0
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
        self._backend_name = "energy"
        self._rms_threshold = rms_threshold
        self._fallback_checked = False
        self._silero_had_speech = False
        self._frames_seen = 0
        self._energy_speech_like_frames = 0

        # Choose VAD backend
        backend = (vad_backend or "energy").lower()
        if backend == "silero":
            try:
                self._vad = _SileroVAD()
                self._backend_name = "silero"
                print(f"[audio] Using Silero ONNX VAD backend (threshold={self._vad._threshold})")
            except Exception as e:
                print(f"[audio] Silero backend unavailable ({e}); trying WebRTC fallback")
                try:
                    self._vad = _WebRTCVAD(mode=vad_mode)
                    self._backend_name = "webrtc"
                    print("[audio] Using WebRTC VAD fallback")
                except Exception as we:
                    print(f"[audio] WebRTC fallback unavailable ({we}); using Energy VAD")
                    silence_frames = max(1, int(vad_silence_duration / (BLOCK_MS / 1000)))
                    self._vad = _EnergyVAD(
                        rms_threshold=rms_threshold,
                        silence_frames=silence_frames,
                    )
                    self._backend_name = "energy"
        elif backend == "webrtc" or USE_WEBRTCVAD:
            try:
                self._vad = _WebRTCVAD(mode=vad_mode)
                self._backend_name = "webrtc"
                print("[audio] Using WebRTC VAD backend")
            except Exception as e:
                print(f"[audio] WebRTC backend unavailable ({e}); using Energy VAD")
                silence_frames = max(1, int(vad_silence_duration / (BLOCK_MS / 1000)))
                self._vad = _EnergyVAD(
                    rms_threshold=rms_threshold,
                    silence_frames=silence_frames,
                )
                self._backend_name = "energy"
        else:
            silence_frames = max(1, int(vad_silence_duration / (BLOCK_MS / 1000)))
            self._vad = _EnergyVAD(
                rms_threshold=rms_threshold,
                silence_frames=silence_frames,
            )
            self._backend_name = "energy"

        self._silence_limit = max(1, int(vad_silence_duration / (BLOCK_MS / 1000)))
        self._max_chunk_frames = max(1, int(max_chunk_duration / (BLOCK_MS / 1000)))
        self._min_speech_frames = 6  # ~180 ms of voiced audio required

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
            self._fallback_checked = False
            self._silero_had_speech = False
            self._frames_seen = 0
            self._energy_speech_like_frames = 0
            if hasattr(self._vad, "reset"):
                self._vad.reset()
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype=DTYPE,
                blocksize=BLOCK_SAMPLES,
                callback=self._audio_callback,
            )
            self._stream.start()
            print("[audio] Input stream started")

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
                print(
                    f"[audio] Flushing on stop: voiced={self._voiced_frames}, "
                    f"frames={len(self._speech_frames)}"
                )
                self._emit(self._speech_frames.copy())
            else:
                print(
                    f"[audio] Stop with insufficient speech: voiced={self._voiced_frames}, "
                    f"frames={len(self._speech_frames)}"
                )
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
        self._frames_seen += 1
        rms = int(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))
        if rms > self._rms_threshold:
            self._energy_speech_like_frames += 1

        # Windows safeguard: if Silero never triggers despite clear energy,
        # fallback automatically to Energy VAD for this session.
        if (
            self._backend_name == "silero"
            and not self._fallback_checked
            and not self._silero_had_speech
            and self._frames_seen >= 180  # ~5.4 seconds
            and self._energy_speech_like_frames >= 50
        ):
            silence_frames = self._silence_limit
            self._vad = _EnergyVAD(
                rms_threshold=self._rms_threshold,
                silence_frames=silence_frames,
            )
            self._backend_name = "energy"
            self._fallback_checked = True
            print(
                "[audio] Silero produced no speech after ~5s with active input; "
                "falling back to Energy VAD"
            )

        is_speech = self._vad.is_speech(frame)
        if self._backend_name == "silero" and is_speech:
            self._silero_had_speech = True

        if is_speech:
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
        duration_s = len(frames) * (BLOCK_MS / 1000)
        print(f"[audio] Emitting chunk: frames={len(frames)}, duration={duration_s:.2f}s")

        asyncio.run_coroutine_threadsafe(
            self._on_chunk(wav_bytes),
            self._loop,
        )
