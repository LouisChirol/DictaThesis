"""
Two-pass dictation pipeline.

Each audio chunk goes through:
  RECEIVED → TRANSCRIBING (Voxtral Mini) → DRAFT → REFINING (batched Small) → FINAL → INJECTED

STT runs per chunk in parallel. Refinement batches consecutive drafts (debounced) so
punctuation and phrasing span chunk boundaries. After a pause, or about every 30s,
Medium may rewrite a trailing owned passage in place (capped, not the whole buffer).
Injection is strictly ordered; slow refinement falls back to draft text after timeouts
so later chunks are not blocked.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import string
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from functools import partial

import api_client
from context_reader import get_foreground_window_info, read_focused_text
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
REFINE_BATCH_MAX_CHUNKS = 6
REFINE_BATCH_DEBOUNCE_S = 0.8
SESSION_COMPLETE_TIMEOUT_S = 120.0
INJECTED_TAIL_CHARS = 800
POLISH_IDLE_S = 2.5
POLISH_INTERVAL_S = 30.0
POLISH_QUIET_S = 1.0
POLISH_TIMEOUT_S = 8.0
POLISH_MIN_CHARS = 80
POLISH_MAX_CHARS = 800
POLISH_MIN_WORD_OVERLAP = 0.35
FIELD_READ_TIMEOUT_S = 2.0


def _result_has_named_command(result: dict) -> bool:
    return any(
        seg.get("command", "none") not in (None, "", "none")
        for seg in result.get("segments", [])
    )


def _coalesce_refine_result(result: dict, combined_draft: str) -> tuple[dict, str]:
    """Keep whitespace-only full_text (line/paragraph breaks). Fall back to draft otherwise."""
    full_text = result.get("full_text")
    if full_text:
        return result, full_text
    if _result_has_named_command(result):
        return result, full_text or ""
    fallback = {**result, "full_text": combined_draft}
    return fallback, combined_draft


def trailing_polish_slice(buffer: str, max_chars: int = POLISH_MAX_CHARS) -> tuple[int, str]:
    """Return (start_index, text) of a trailing passage to rewrite, not the whole buffer."""
    if not buffer:
        return 0, ""
    if len(buffer) <= max_chars:
        return 0, buffer
    start = len(buffer) - max_chars
    window = buffer[start:]
    match = re.search(r"(?:\n\n+|[.!?]\s+)", window)
    if match and len(window) - match.end() >= POLISH_MIN_CHARS:
        start += match.end()
        window = buffer[start:]
    elif start > 0:
        sp = window.find(" ")
        if sp != -1 and len(window) - sp - 1 >= POLISH_MIN_CHARS:
            start += sp + 1
            window = buffer[start:]
    return start, window


def _rewrite_looks_sane(original: str, rewritten: str) -> bool:
    """Reject clipboard leftovers / instruction-like output that barely overlaps the source."""
    if not rewritten.strip():
        return False
    leak_markers = (
        "flush the rewrite",
        "alt-tab",
        "system prompt",
        "json schema",
        "you rewrite",
        "owned suffix",
    )
    low = rewritten.lower()
    if any(marker in low for marker in leak_markers):
        return False
    orig_words = set(re.findall(r"\w{4,}", original.lower(), flags=re.UNICODE))
    new_words = set(re.findall(r"\w{4,}", rewritten.lower(), flags=re.UNICODE))
    if len(orig_words) >= 8:
        overlap = len(orig_words & new_words) / len(orig_words)
        if overlap < POLISH_MIN_WORD_OVERLAP:
            return False
    return True


def _preview_text(text: str, n: int = 100) -> str:
    compact = text.replace("\r", "\\r").replace("\n", "\\n")
    if len(compact) <= n:
        return compact
    half = n // 2
    return compact[:half] + "…" + compact[-half:]


def _field_suffix_matches(field: str, paragraph: str) -> bool:
    norm_f = field.replace("\r\n", "\n")
    norm_p = paragraph.replace("\r\n", "\n")
    return norm_f.endswith(norm_p)


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
        self._mutation_lock = asyncio.Lock()
        self._polish_lock = asyncio.Lock()

        self._editor = TextFieldEditor()
        self._active = False
        self._tasks: list[asyncio.Task] = []
        self._inject_task: asyncio.Task | None = None
        self._refine_task: asyncio.Task | None = None
        self._polish_task: asyncio.Task | None = None
        self._complete_task: asyncio.Task | None = None
        self._rewrite_eligible = False
        self._inject_focus_id: str | None = None
        self._inject_focus_title: str | None = None
        self._last_inject_at: float = 0.0
        self._last_polished_hash: str | None = None
        self._last_polish_log_key: tuple | None = None
        self._last_polish_attempt_at: float = 0.0
        self._last_polish_done_at: float = 0.0
        self._session_started_at: float = 0.0

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
        self._rewrite_eligible = False
        self._inject_focus_id = None
        self._inject_focus_title = None
        self._last_inject_at = 0.0
        self._last_polished_hash = None
        self._last_polish_log_key = None
        self._last_polish_attempt_at = 0.0
        self._last_polish_done_at = 0.0
        self._session_started_at = time.time()
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
        self._cancel_worker_tasks()
        self._inject_task = loop.create_task(self._injection_worker())
        self._refine_task = loop.create_task(self._refine_worker())
        self._polish_task = loop.create_task(self._polish_worker())

    def stop_session(self):
        if not self._active:
            return
        self._active = False
        self._refine_event.set()
        self._inject_event.set()
        loop = asyncio.get_event_loop()
        if self._complete_task and not self._complete_task.done():
            self._complete_task.cancel()
        self._complete_task = loop.create_task(self._complete_session())

    async def _cancel_worker_tasks(self):
        for task in (self._inject_task, self._refine_task, self._polish_task, self._complete_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._inject_task = None
        self._refine_task = None
        self._polish_task = None
        self._complete_task = None

    def _has_inflight_transcribe_tasks(self) -> bool:
        return any(not t.done() for t in self._tasks)

    def _has_inflight_work(self) -> bool:
        if self._draft_ready or self._has_inflight_transcribe_tasks():
            return True
        return any(
            i not in self._finalized
            for i in range(self._next_inject_index, self._chunk_counter)
        )

    async def _complete_session(self):
        """Wait for STT, refinement, and injection to finish after Stop."""
        print("[pipeline] Session completing — draining pending chunks")
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

        deadline = time.time() + SESSION_COMPLETE_TIMEOUT_S
        while self._has_inflight_work() and time.time() < deadline:
            self._refine_event.set()
            self._inject_event.set()
            await asyncio.sleep(0.15)

        if self._has_inflight_work():
            print("[pipeline] Session drain timeout — forcing remaining chunks")
            await self._force_drain_remaining()

        await self._try_polish("session_stop")

        print("[pipeline] Session complete")
        if self._on_state_change:
            self._on_state_change(False)

    async def _force_drain_remaining(self):
        """Last resort: emit drafts and empty results so injection can finish."""
        for index in range(self._next_inject_index, self._chunk_counter):
            if index in self._finalized:
                continue
            if await self._force_emit_draft(index):
                continue
            chunk = self._index_to_chunk.get(index)
            if chunk and chunk.state == ChunkState.TRANSCRIBING:
                chunk.state = ChunkState.ERROR
            self._signal_finalized(index, "")

        deadline = time.time() + 10.0
        while self._has_inflight_work() and time.time() < deadline:
            self._refine_event.set()
            self._inject_event.set()
            await asyncio.sleep(0.15)

    def inject_literal(self, text: str) -> None:
        """Inject literal text from HUD quick-insert menu (outside chunk flow)."""
        if not text:
            return
        if self._settings.get("enable_injection"):
            inject_text(text)
        self._session_buffer += text
        self._last_inject_at = time.time()
        self._rewrite_eligible = True
        self._capture_inject_focus()

    def clear_context(self) -> None:
        """Clear session memory used for STT/LLM continuity (not pasted document text)."""
        self._session_buffer = ""
        self._session_context = []
        self._document_prefix = ""
        self._rewrite_eligible = False
        self._last_polished_hash = None
        self._last_inject_at = 0.0
        self._last_polish_log_key = None
        self._last_polish_attempt_at = 0.0
        self._last_polish_done_at = 0.0
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

    def _injected_tail(self, max_chars: int = INJECTED_TAIL_CHARS) -> str:
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
                    await asyncio.sleep(0.1)
                    continue
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
                    f"({len(batch_indices)} chunks, model={api_client.REFINEMENT_MODEL}): "
                    f"{combined_draft[:80]!r}"
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
                    result, final_text = _coalesce_refine_result(result, combined_draft)
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
        """Inject text with whitespace heuristic and buffer tracking.

        Whitespace-only payloads (line break / paragraph) must still be pasted.
        """
        if not text:
            return

        if self._needs_space_before(text):
            text = " " + text

        if self._settings.get("enable_injection"):
            preview = text[:60] if text.strip() else text
            print(f"[pipeline] Injecting chunk {idx}: {preview!r}...")
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
            if full_text:
                await self._inject_with_spacing(full_text, idx)
                return
            # Command-only structure (newline / paragraph) with empty full_text
            await self._dispatch_segments(result, idx, cmd_lookup)
            return

        await self._dispatch_segments(result, idx, cmd_lookup)

    async def _dispatch_segments(self, result: dict, idx: int, cmd_lookup: dict):
        """Walk LLM segments and inject text or run editing/control commands."""
        for seg in result.get("segments", []):
            cmd_id = seg.get("command", "none")
            content = seg.get("content", "")
            cmd_def = cmd_lookup.get(cmd_id) if cmd_id not in (None, "", "none") else None
            action = (cmd_def or {}).get("action", {})
            category = (cmd_def or {}).get("category", "")

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
            elif content:
                await self._inject_with_spacing(content, idx)

    def _capture_inject_focus(self) -> None:
        wid, title = get_foreground_window_info()
        self._inject_focus_id = wid
        self._inject_focus_title = title
        print(
            f"[polish] caret_anchor hwnd={wid!r} title={title!r} "
            f"buffer_chars={len(self._session_buffer)}"
        )

    async def _polish_worker(self):
        """Rewrite a trailing owned passage after a pause, or about every 30s."""
        while self._active:
            await asyncio.sleep(0.35)
            if not self._active:
                break
            if self._has_inflight_work():
                continue
            if self._last_inject_at <= 0:
                continue
            now = time.time()
            quiet = now - self._last_inject_at
            if quiet < POLISH_QUIET_S:
                continue
            interval_due = (
                (
                    self._last_polish_done_at <= 0
                    and now - self._session_started_at >= POLISH_INTERVAL_S
                )
                or (
                    self._last_polish_done_at > 0
                    and now - self._last_polish_done_at >= POLISH_INTERVAL_S
                )
            )
            if quiet >= POLISH_IDLE_S:
                await self._try_polish("idle")
            elif interval_due:
                await self._try_polish("interval")

    async def _try_polish(self, reason: str) -> None:
        async with self._polish_lock:
            await self._try_polish_locked(reason)

    async def _try_polish_locked(self, reason: str) -> None:
        loop = asyncio.get_event_loop()
        start, paragraph = trailing_polish_slice(self._session_buffer)
        para_hash = hashlib.sha256(paragraph.encode("utf-8")).hexdigest()[:12]
        stripped_len = len(paragraph.strip())
        log_key_base = (reason, para_hash)

        def skip(code: str, extra: str = "") -> None:
            key = (*log_key_base, code)
            if self._last_polish_log_key == key:
                return
            self._last_polish_log_key = key
            suffix = f" {extra}" if extra else ""
            print(f"[polish] skip reason={code}{suffix}")

        if not self._rewrite_eligible:
            skip("not_eligible")
            return
        if stripped_len < POLISH_MIN_CHARS:
            skip("too_short", f"min={POLISH_MIN_CHARS} stripped={stripped_len}")
            return
        if para_hash == self._last_polished_hash:
            return
        if self._has_inflight_work() and reason != "session_stop":
            skip("inflight_work")
            return
        if not self._settings.get("enable_injection"):
            skip("injection_disabled")
            return
        if reason == "idle" and time.time() - self._last_polish_attempt_at < POLISH_IDLE_S:
            return
        if (
            reason == "interval"
            and self._last_polish_done_at > 0
            and time.time() - self._last_polish_done_at < POLISH_INTERVAL_S
        ):
            return
        self._last_polish_attempt_at = time.time()

        print(
            f"[polish] consider reason={reason} eligible={self._rewrite_eligible} "
            f"start={start} chars={len(paragraph)} stripped={stripped_len} "
            f"hash={para_hash} preview={_preview_text(paragraph)!r}"
        )

        hwnd_now, title_now = await loop.run_in_executor(None, get_foreground_window_info)
        hwnd_match = bool(
            self._inject_focus_id and hwnd_now and hwnd_now == self._inject_focus_id
        )
        print(
            f"[polish] focus inject_hwnd={self._inject_focus_id!r} "
            f"inject_title={self._inject_focus_title!r} "
            f"now_hwnd={hwnd_now!r} now_title={title_now!r} hwnd_match={hwnd_match}"
        )

        field = await loop.run_in_executor(
            None, partial(read_focused_text, timeout=FIELD_READ_TIMEOUT_S)
        )
        if field is None:
            print("[polish] field_read=unavailable")
            if not hwnd_match:
                if (
                    hwnd_now
                    and self._inject_focus_id
                    and hwnd_now != self._inject_focus_id
                ):
                    self._rewrite_eligible = False
                    skip("focus_changed", "flushed_eligible")
                    return
                skip("no_caret_confidence")
                return
            print("[polish] caret_confidence=hwnd_only")
        else:
            suffix_ok = _field_suffix_matches(field, paragraph)
            print(
                f"[polish] field_read=ok field_chars={len(field)} "
                f"suffix_match={suffix_ok} field_tail={_preview_text(field[-120:])!r}"
            )
            if not suffix_ok:
                self._rewrite_eligible = False
                skip("field_suffix_mismatch", "flushed_eligible")
                return
            print("[polish] caret_confidence=field_suffix")

        api_key = self._settings.get("api_key")
        if not api_key:
            skip("no_api_key")
            return

        preceding = (self._document_prefix + self._session_buffer[:start])[-400:]
        print(
            f"[polish] api_call model={api_client.POLISH_MODEL} "
            f"timeout={POLISH_TIMEOUT_S}s preceding_chars={len(preceding)}"
        )
        try:
            parsed = await asyncio.wait_for(
                api_client.polish_paragraph(
                    paragraph,
                    api_key,
                    self._settings,
                    preceding_tail=preceding,
                ),
                timeout=POLISH_TIMEOUT_S,
            )
        except TimeoutError:
            skip("api_timeout", f"seconds={POLISH_TIMEOUT_S}")
            return
        except Exception as e:
            skip("api_error", f"error={e}")
            return

        if not parsed:
            skip("api_empty")
            return

        rewritten = parsed.get("rewritten") or ""
        changed = bool(parsed.get("changed", rewritten != paragraph))
        print(
            f"[polish] api_ok changed={changed} rewritten_chars={len(rewritten)} "
            f"preview={_preview_text(rewritten)!r}"
        )
        if not rewritten.strip():
            skip("empty_rewrite")
            return
        if not _rewrite_looks_sane(paragraph, rewritten):
            skip(
                "rewrite_mismatch",
                f"preview={_preview_text(rewritten)!r}",
            )
            return
        if not changed or rewritten == paragraph:
            self._last_polished_hash = para_hash
            self._last_polish_done_at = time.time()
            skip("unchanged", "marked_polished")
            return
        if len(paragraph) > POLISH_MAX_CHARS:
            skip("window_too_large", f"chars={len(paragraph)} max={POLISH_MAX_CHARS}")
            return

        async with self._mutation_lock:
            if self._has_inflight_work() and reason != "session_stop":
                skip("raced_inflight")
                return
            current = self._session_buffer[start:]
            if current != paragraph:
                skip(
                    "buffer_changed",
                    f"expected={_preview_text(paragraph)!r} got={_preview_text(current)!r}",
                )
                return
            print(
                f"[polish] apply replace_backwards count={len(paragraph)} "
                f"new_chars={len(rewritten)}"
            )
            await loop.run_in_executor(
                None,
                partial(
                    self._editor.replace_backwards,
                    len(paragraph),
                    rewritten,
                ),
            )
            self._session_buffer = self._session_buffer[:start] + rewritten
            self._last_polished_hash = hashlib.sha256(rewritten.encode("utf-8")).hexdigest()[
                :12
            ]
            self._last_inject_at = time.time()
            self._last_polish_done_at = time.time()

        post_field = await loop.run_in_executor(
            None, partial(read_focused_text, timeout=FIELD_READ_TIMEOUT_S)
        )
        if post_field is None:
            print("[polish] post_read=unavailable applied=assumed")
        else:
            post_ok = _field_suffix_matches(post_field, rewritten)
            print(
                f"[polish] post_read=ok suffix_match={post_ok} "
                f"field_chars={len(post_field)} tail={_preview_text(post_field[-120:])!r}"
            )
        print(
            f"[polish] done reason={reason} hash={self._last_polished_hash} "
            f"buffer_chars={len(self._session_buffer)}"
        )

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
                before = self._session_buffer
                async with self._mutation_lock:
                    await self._dispatch_result(result, idx)
                if self._session_buffer != before:
                    self._last_inject_at = time.time()
                    self._rewrite_eligible = True
                    self._capture_inject_focus()

            if not self._active and not self._has_unfinalized_chunks():
                await asyncio.sleep(0.1)
                if not self._has_unfinalized_chunks():
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
