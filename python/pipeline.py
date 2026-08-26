"""
Two-pass dictation pipeline.

Each audio chunk goes through:
  RECEIVED → TRANSCRIBING (Voxtral Mini) → DRAFT → REFINING (batched Medium) → FINAL → INJECTED

STT runs per chunk in parallel. Refinement batches consecutive drafts (debounced) so
punctuation and phrasing span chunk boundaries. Injection is strictly ordered; slow
refinement falls back to draft text after timeouts so later chunks are not blocked.
"""

from __future__ import annotations

import asyncio
import re
import string
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

import api_client
from context_reader import read_focused_text
from injector import inject_text
from text_editor import TextFieldEditor

SILENCE_HALLUCINATION_PATTERNS = {
    "non",
    "ah",
    "euh",
    "hum",
    "hmm",
    "merci",
    "merci beaucoup",
    "thank you",
    "thanks",
}

DELIMITER_PAIRS = (("(", ")"), ("«", "»"), ("[", "]"))

# Refinement batching and timeout tuning
REFINE_TIMEOUT_S = 5.0
HEAD_BLOCK_TIMEOUT_S = 3.0
REFINE_BATCH_MAX_CHUNKS = 4
REFINE_BATCH_DEBOUNCE_S = 0.6


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
        self._index_to_chunk: dict[int, Chunk] = {}
        self._session_context: list[str] = []  # rolling last-5 finalized texts
        self._session_buffer: str = ""  # all text injected/produced this session
        self._document_prefix: str = ""  # text in the field before dictation started

        # Ordered injection: tracks which chunk index to inject next
        self._next_inject_index: int = 0
        self._chunk_counter: int = 0
        self._finalized: dict[int, dict | str] = {}  # index → result (ready to inject)
        self._inject_event = asyncio.Event()

        # Batched refinement queue (index → chunk with draft, waiting for pass 2)
        self._draft_ready: dict[int, Chunk] = {}
        self._refine_event = asyncio.Event()
        self._refine_lock = asyncio.Lock()

        self._editor = TextFieldEditor()
        self._active = False
        self._tasks: list[asyncio.Task] = []

    # ------------------------------------------------------------------
    # Session control
    # ------------------------------------------------------------------

    def start_session(self):
        self._active = True
        self._session_context = []
        self._session_buffer = ""
        self._document_prefix = ""
        self._next_inject_index = 0
        self._chunk_counter = 0
        self._finalized = {}
        self._draft_ready = {}
        self._index_to_chunk = {}
        self._tasks = []
        try:
            prefix = read_focused_text()
            if prefix:
                self._document_prefix = prefix
                print(f"[pipeline] Read {len(prefix)} chars of document context")
        except Exception as e:
            print(f"[pipeline] Context read failed (non-fatal): {e}")

        if self._on_state_change:
            self._on_state_change(True)
        loop = asyncio.get_event_loop()
        self._inject_task = loop.create_task(self._injection_worker())
        self._refine_task = loop.create_task(self._refine_worker())

    def stop_session(self):
        self._active = False
        self._refine_event.set()
        if self._on_state_change:
            self._on_state_change(False)

    def inject_literal(self, text: str) -> None:
        """Inject literal text from HUD quick-insert menu (outside chunk flow)."""
        if not text:
            return
        if self._settings.get("enable_injection"):
            inject_text(text)
        self._session_buffer += text

    def clear_context(self) -> None:
        """Clear session memory used for STT/LLM continuity (not pasted document text)."""
        self._session_buffer = ""
        self._session_context = []
        self._document_prefix = ""
        try:
            prefix = read_focused_text()
            if prefix:
                self._document_prefix = prefix
                print(f"[pipeline] Re-read {len(prefix)} chars of document context after clear")
        except Exception as e:
            print(f"[pipeline] Context re-read after clear failed (non-fatal): {e}")
        print("[pipeline] Session context cleared")

    # ------------------------------------------------------------------
    # Entry point for audio chunks
    # ------------------------------------------------------------------

    async def on_chunk(self, wav_bytes: bytes):
        """Called by AudioCapture when a speech segment is ready."""
        index = self._chunk_counter
        self._chunk_counter += 1

        chunk = Chunk(index=index)
        self._chunks[chunk.id] = chunk
        self._index_to_chunk[index] = chunk

        task = asyncio.get_event_loop().create_task(self._process_chunk(chunk, wav_bytes))
        self._tasks.append(task)

    # ------------------------------------------------------------------
    # Internal pipeline
    # ------------------------------------------------------------------

    def _document_tail(self, max_chars: int = 1000) -> str:
        return (self._document_prefix[-500:] + self._session_buffer)[-max_chars:]

    def _injected_tail(self, max_chars: int = 200) -> str:
        return self._document_tail(max_chars=max_chars)[-max_chars:]

    @staticmethod
    def _compute_open_delimiters(buffer: str) -> list[str]:
        """Return delimiter characters that are still open in the session buffer."""
        open_chars: list[str] = []
        for open_ch, close_ch in DELIMITER_PAIRS:
            if buffer.count(open_ch) > buffer.count(close_ch):
                open_chars.append(open_ch)
        return open_chars

    @staticmethod
    def _is_silence_hallucination(text: str) -> bool:
        normalized = re.sub(r"[^\w\s]", " ", text.lower(), flags=re.UNICODE)
        normalized = " ".join(normalized.split())
        if not normalized:
            return True
        if normalized in SILENCE_HALLUCINATION_PATTERNS:
            return True
        tokens = normalized.split()
        return len(tokens) == 1 and len(tokens[0]) <= 2

    async def _process_chunk(self, chunk: Chunk, wav_bytes: bytes):
        api_key = self._settings.get("api_key")
        language = self._settings.get("language")
        lang_hint = language if language != "auto" else "fr"
        injected_tail = self._injected_tail()
        vocabulary = self._settings.get("vocabulary") or []

        # --- 1st pass: Voxtral Mini Transcribe ---
        chunk.state = ChunkState.TRANSCRIBING
        try:
            draft = await api_client.transcribe(
                wav_bytes,
                api_key,
                lang_hint,
                injected_tail=injected_tail,
                vocabulary=vocabulary,
            )
        except Exception as e:
            print(f"[pipeline] Transcription error for chunk {chunk.id}: {e}")
            chunk.state = ChunkState.ERROR
            self._signal_finalized(chunk.index, "")
            return

        if not draft:
            self._signal_finalized(chunk.index, "")
            return
        if self._is_silence_hallucination(draft):
            self._signal_finalized(chunk.index, "")
            return

        chunk.draft_text = draft
        chunk.state = ChunkState.DRAFT
        print(f"[pipeline] Draft chunk {chunk.index}: {draft[:80]!r}")

        if self._on_draft:
            self._on_draft(chunk.id, draft)

        # Queue for batched pass-2 refinement
        chunk.state = ChunkState.REFINING
        self._draft_ready[chunk.index] = chunk
        self._refine_event.set()

    @staticmethod
    def _join_drafts(texts: list[str]) -> str:
        parts = [t.strip() for t in texts if t and t.strip()]
        return " ".join(parts)

    def _draft_fallback_result(self, draft_text: str) -> dict:
        return {
            "segments": [{"type": "text", "content": draft_text, "command": "none"}],
            "full_text": draft_text,
            "detected_language": "fr",
        }

    async def _refine_worker(self):
        """Batch consecutive drafts and run a single Medium refinement per batch."""
        while True:
            if not self._draft_ready:
                if not self._active:
                    break
                self._refine_event.clear()
                await self._refine_event.wait()
                continue

            debounce = 0.0 if not self._active else REFINE_BATCH_DEBOUNCE_S
            if debounce > 0:
                await asyncio.sleep(debounce)

            async with self._refine_lock:
                if not self._draft_ready:
                    continue

                indices = sorted(self._draft_ready.keys())
                start = indices[0]
                while start in self._finalized:
                    self._draft_ready.pop(start, None)
                    start += 1

                batch_indices: list[int] = []
                for i in range(start, start + REFINE_BATCH_MAX_CHUNKS):
                    if i in self._draft_ready:
                        batch_indices.append(i)
                    else:
                        break

                if not batch_indices:
                    continue

                chunks = [self._draft_ready.pop(i) for i in batch_indices]
                combined_draft = self._join_drafts([c.draft_text or "" for c in chunks])
                if not combined_draft:
                    for idx in batch_indices:
                        self._signal_finalized(idx, "")
                    continue

                print(
                    f"[pipeline] Refining batch {batch_indices[0]}-{batch_indices[-1]} "
                    f"({len(batch_indices)} chunks): {combined_draft[:80]!r}"
                )

                api_key = self._settings.get("api_key")
                mode = self._settings.get("mode")
                injected_tail = self._injected_tail()
                open_delimiters = self._compute_open_delimiters(self._session_buffer)

                try:
                    result = await asyncio.wait_for(
                        api_client.refine(
                            combined_draft,
                            api_key,
                            self._session_context,
                            self._settings,
                            mode,
                            injected_tail=injected_tail,
                            open_delimiters=open_delimiters,
                        ),
                        timeout=REFINE_TIMEOUT_S,
                    )
                    final_text = result.get("full_text") or combined_draft
                except TimeoutError:
                    print(
                        f"[pipeline] Refinement timeout ({REFINE_TIMEOUT_S}s) "
                        f"for batch {batch_indices[0]}-{batch_indices[-1]}; using draft"
                    )
                    final_text = combined_draft
                    result = self._draft_fallback_result(combined_draft)
                except Exception as e:
                    print(
                        f"[pipeline] Refinement error for batch "
                        f"{batch_indices[0]}-{batch_indices[-1]}: {e}"
                    )
                    final_text = combined_draft
                    result = self._draft_fallback_result(combined_draft)

                commands = self._settings.get("dictation_commands") or []
                cmd_lookup = {cmd["id"]: cmd for cmd in commands}
                stop_requested = False
                for seg in result.get("segments", []):
                    cmd_def = cmd_lookup.get(seg.get("command", "none"))
                    if cmd_def and cmd_def.get("category") == "control":
                        action = cmd_def.get("action", {})
                        if action.get("control") == "stop_dictation":
                            stop_requested = True

                for i, chunk in enumerate(chunks):
                    if i == 0:
                        chunk.final_text = final_text
                        if self._on_final:
                            self._on_final(chunk.id, final_text)
                        self._signal_finalized(batch_indices[0], result)
                    else:
                        chunk.final_text = ""
                        if self._on_final:
                            self._on_final(chunk.id, "")
                        self._signal_finalized(batch_indices[i], "")

                    chunk.state = ChunkState.FINAL

                if final_text.strip():
                    self._session_context.append(final_text.strip())
                    if len(self._session_context) > 5:
                        self._session_context.pop(0)

                if stop_requested:
                    self.stop_session()

    async def _force_emit_draft(self, index: int) -> bool:
        """Emit raw draft for a blocked index so later chunks can inject."""
        async with self._refine_lock:
            if index in self._finalized:
                return True

            chunk = self._index_to_chunk.get(index)
            if not chunk or not chunk.draft_text:
                return False

            self._draft_ready.pop(index, None)
            draft = chunk.draft_text
            result = self._draft_fallback_result(draft)

            print(
                f"[pipeline] Head-of-line timeout ({HEAD_BLOCK_TIMEOUT_S}s) "
                f"for chunk {index}; emitting draft"
            )

            chunk.final_text = draft
            chunk.state = ChunkState.FINAL
            if self._on_final:
                self._on_final(chunk.id, draft)

            if draft.strip():
                self._session_context.append(draft.strip())
                if len(self._session_context) > 5:
                    self._session_context.pop(0)

            self._signal_finalized(index, result)
            return True

    def _signal_finalized(self, index: int, result: dict | str):
        """Mark a chunk as ready for ordered injection."""
        self._finalized[index] = result
        loop = asyncio.get_event_loop()
        loop.call_soon_threadsafe(self._inject_event.set)

    # ------------------------------------------------------------------
    # Ordered injection worker
    # ------------------------------------------------------------------

    def _needs_space_before(self, text: str) -> bool:
        """Check if a space should be prepended before injecting text."""
        return (
            bool(self._session_buffer)
            and self._session_buffer[-1] not in string.whitespace
            and text[0] not in string.punctuation + string.whitespace
        )

    async def _inject_with_spacing(self, text: str, idx: int):
        """Inject text with whitespace heuristic and buffer tracking."""
        if not text.strip():
            return

        if self._needs_space_before(text):
            text = " " + text

        if self._settings.get("enable_injection"):
            print(f"[pipeline] Injecting chunk {idx}: {text[:60]!r}...")
            await asyncio.get_event_loop().run_in_executor(None, inject_text, text)
            print(f"[pipeline] Injection done for chunk {idx}")
        else:
            print(f"[pipeline] Injection disabled; keeping chunk {idx} in HUD only")

        self._session_buffer += text

    async def _handle_editing_command(self, cmd_id: str, content: str, action: dict, idx: int):
        """Execute an editing command using the session buffer and TextFieldEditor."""
        edit_type = action.get("edit", "")
        loop = asyncio.get_event_loop()

        if not self._settings.get("enable_injection"):
            print(f"[pipeline] Editing command '{cmd_id}' skipped (injection disabled)")
            return

        if edit_type == "delete_previous_sentence":
            count = TextFieldEditor.find_last_sentence_length(self._session_buffer)
            if count > 0:
                print(f"[pipeline] Deleting last sentence ({count} chars)")
                await loop.run_in_executor(None, self._editor.delete_backwards, count)
                self._session_buffer = self._session_buffer[:-count]
            else:
                print("[pipeline] No sentence to delete in session buffer")

        elif edit_type == "delete_previous_word":
            count = TextFieldEditor.find_last_word_length(self._session_buffer)
            if count > 0:
                print(f"[pipeline] Deleting last word ({count} chars)")
                await loop.run_in_executor(None, self._editor.delete_backwards, count)
                self._session_buffer = self._session_buffer[:-count]
            else:
                print("[pipeline] No word to delete in session buffer")

        else:
            print(f"[pipeline] Unknown editing command: {edit_type}")

    async def _dispatch_result(self, result: dict | str, idx: int):
        """Dispatch a finalized result — either plain text or structured LLM response."""
        if isinstance(result, str):
            await self._inject_with_spacing(result, idx)
            return

        commands = self._settings.get("dictation_commands") or []
        cmd_lookup = {cmd["id"]: cmd for cmd in commands}
        segments = result.get("segments", [])

        has_special_commands = any(
            cmd_lookup.get(seg.get("command", "none"), {}).get("category")
            in ("editing", "llm_instructed")
            for seg in segments
        )

        if not has_special_commands:
            full_text = result.get("full_text", "")
            await self._inject_with_spacing(full_text, idx)
            return

        for seg in segments:
            cmd_id = seg.get("command", "none")
            content = seg.get("content", "")
            cmd_def = cmd_lookup.get(cmd_id)

            if seg.get("type") == "text" or cmd_id == "none":
                if content:
                    await self._inject_with_spacing(content, idx)
                continue

            if not cmd_def:
                if content:
                    await self._inject_with_spacing(content, idx)
                continue

            category = cmd_def.get("category", "")
            action = cmd_def.get("action", {})

            if category == "formatting":
                text = action.get("text", content)
                if "__N__" in text:
                    text = text.replace("__N__", content)
                await self._inject_with_spacing(text, idx)

            elif category == "control":
                pass

            elif category == "editing":
                await self._handle_editing_command(cmd_id, content, action, idx)

            elif category == "llm_instructed":
                full_text = result.get("full_text", "")
                if full_text:
                    await self._inject_with_spacing(full_text, idx)
                break

    def _has_unfinalized_chunks(self) -> bool:
        return any(
            i not in self._finalized
            for i in range(self._next_inject_index, self._chunk_counter)
        )

    async def _injection_worker(self):
        """
        Drains _finalized in strict index order, injecting text into the
        focused app. Falls back to draft text if head-of-line refinement blocks
        later-ready chunks.
        """
        head_blocked_since: float | None = None

        while True:
            while self._next_inject_index in self._finalized:
                head_blocked_since = None
                result = self._finalized.pop(self._next_inject_index)
                idx = self._next_inject_index
                self._next_inject_index += 1
                await self._dispatch_result(result, idx)

            if not self._active and not self._has_unfinalized_chunks():
                break

            waiting_idx = self._next_inject_index
            later_ready = any(i > waiting_idx for i in self._finalized)

            if later_ready and waiting_idx not in self._finalized:
                if head_blocked_since is None:
                    head_blocked_since = time.time()
                elif (
                    time.time() - head_blocked_since >= HEAD_BLOCK_TIMEOUT_S
                    and await self._force_emit_draft(waiting_idx)
                ):
                    head_blocked_since = None
                    continue
            else:
                head_blocked_since = None

            self._inject_event.clear()
            try:
                await asyncio.wait_for(self._inject_event.wait(), timeout=0.5)
            except TimeoutError:
                continue
