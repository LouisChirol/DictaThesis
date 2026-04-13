"""
Two-pass dictation pipeline.

Each audio chunk goes through:
  RECEIVED → TRANSCRIBING (Voxtral) → DRAFT → REFINING (Mistral) → FINAL → INJECTED

Chunks are processed in parallel (asyncio tasks) but injected into the target
app in strict dictation order via an ordered injection queue.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

import api_client
from injector import inject_text


class ChunkState(Enum):
    RECEIVED = "received"
    TRANSCRIBING = "transcribing"
    DRAFT = "draft"
    REFINING = "refining"
    FINAL = "final"
    INJECTED = "injected"
    ERROR = "error"


@dataclass
class Chunk:
    index: int
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    state: ChunkState = ChunkState.RECEIVED
    draft_text: str | None = None
    final_text: str | None = None
    created_at: float = field(default_factory=time.time)


class Pipeline:
    """
    Orchestrates the two-pass transcription pipeline.

    Args:
        settings:        SettingsStore instance.
        on_draft:        Callback(chunk_id, draft_text) — called from asyncio thread.
        on_final:        Callback(chunk_id, final_text) — called from asyncio thread.
        on_state_change: Callback(is_active: bool) — for tray icon / HUD status.
    """

    def __init__(
        self,
        settings,
        on_draft: Callable[[str, str], None] | None = None,
        on_final: Callable[[str, str], None] | None = None,
        on_state_change: Callable[[bool], None] | None = None,
    ):
        self._settings = settings
        self._on_draft = on_draft
        self._on_final = on_final
        self._on_state_change = on_state_change

        self._chunks: dict[str, Chunk] = {}
        self._session_context: list[str] = []  # rolling last-5 finalized texts

        # Ordered injection: tracks which chunk index to inject next
        self._next_inject_index: int = 0
        self._chunk_counter: int = 0
        self._finalized: dict[int, str] = {}  # index → final_text (ready to inject)
        self._inject_event = asyncio.Event()

        self._active = False
        self._tasks: list[asyncio.Task] = []

    # ------------------------------------------------------------------
    # Session control
    # ------------------------------------------------------------------

    def start_session(self):
        self._active = True
        self._session_context = []
        self._next_inject_index = 0
        self._chunk_counter = 0
        self._finalized = {}
        self._tasks = []
        if self._on_state_change:
            self._on_state_change(True)
        # Start the injection worker
        self._inject_task = asyncio.get_event_loop().create_task(self._injection_worker())

    def stop_session(self):
        self._active = False
        if self._on_state_change:
            self._on_state_change(False)

    # ------------------------------------------------------------------
    # Entry point for audio chunks
    # ------------------------------------------------------------------

    async def on_chunk(self, wav_bytes: bytes):
        """Called by AudioCapture when a speech segment is ready."""
        index = self._chunk_counter
        self._chunk_counter += 1

        chunk = Chunk(index=index)
        self._chunks[chunk.id] = chunk

        task = asyncio.get_event_loop().create_task(self._process_chunk(chunk, wav_bytes))
        self._tasks.append(task)

    # ------------------------------------------------------------------
    # Internal pipeline
    # ------------------------------------------------------------------

    async def _process_chunk(self, chunk: Chunk, wav_bytes: bytes):
        api_key = self._settings.get("api_key")
        language = self._settings.get("language")
        mode = self._settings.get("mode")

        # --- 1st pass: Voxtral ---
        chunk.state = ChunkState.TRANSCRIBING
        try:
            lang_hint = language if language != "auto" else "fr"
            draft = await api_client.transcribe(wav_bytes, api_key, lang_hint)
        except Exception as e:
            print(f"[pipeline] Transcription error for chunk {chunk.id}: {e}")
            chunk.state = ChunkState.ERROR
            self._signal_finalized(chunk.index, "")
            return

        if not draft:
            # Silent or empty segment — skip
            self._signal_finalized(chunk.index, "")
            return

        chunk.draft_text = draft
        chunk.state = ChunkState.DRAFT

        if self._on_draft:
            self._on_draft(chunk.id, draft)

        # --- 2nd pass: Mistral LLM ---
        chunk.state = ChunkState.REFINING
        try:
            result = await api_client.refine(
                draft, api_key, self._session_context, self._settings, mode
            )
            final_text = result.get("full_text") or draft
        except Exception as e:
            print(f"[pipeline] Refinement error for chunk {chunk.id}: {e}")
            final_text = draft  # graceful degradation

        # Check for stop_dictation command
        stop_requested = any(
            seg.get("command") == "stop_dictation"
            for seg in result.get("segments", [])
            if isinstance(result, dict)
        )

        # Strip any trailing stop command text from final_text
        chunk.final_text = final_text
        chunk.state = ChunkState.FINAL

        if self._on_final:
            self._on_final(chunk.id, final_text)

        # Update rolling context
        if final_text.strip():
            self._session_context.append(final_text.strip())
            if len(self._session_context) > 5:
                self._session_context.pop(0)

        # Signal injection worker
        self._signal_finalized(chunk.index, final_text)

        if stop_requested:
            self.stop_session()

    def _signal_finalized(self, index: int, text: str):
        """Mark a chunk as ready for ordered injection."""
        self._finalized[index] = text
        # Wake up the injection worker
        loop = asyncio.get_event_loop()
        loop.call_soon_threadsafe(self._inject_event.set)

    # ------------------------------------------------------------------
    # Ordered injection worker
    # ------------------------------------------------------------------

    async def _injection_worker(self):
        """
        Drains _finalized in strict index order, injecting text into the
        focused app. Runs until the pipeline is stopped and all chunks
        have been processed.
        """
        while True:
            # Inject everything that's ready in order
            while self._next_inject_index in self._finalized:
                text = self._finalized.pop(self._next_inject_index)
                self._next_inject_index += 1
                if text.strip():
                    # inject_text is blocking — run in executor
                    await asyncio.get_event_loop().run_in_executor(None, inject_text, text)

            # Check if session is done and all chunks injected
            if not self._active and self._next_inject_index >= self._chunk_counter:
                break

            # Wait for the next chunk to be finalized
            self._inject_event.clear()
            try:
                await asyncio.wait_for(self._inject_event.wait(), timeout=30.0)
            except TimeoutError:
                break
