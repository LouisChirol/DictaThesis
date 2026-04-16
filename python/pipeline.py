"""
Two-pass dictation pipeline.

Each audio chunk goes through:
  RECEIVED → TRANSCRIBING (Voxtral) → DRAFT → REFINING (Mistral) → FINAL → INJECTED

Chunks are processed in parallel (asyncio tasks) but injected into the target
app in strict dictation order via an ordered injection queue.
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
        self._session_buffer: str = ""  # all text injected/produced this session
        self._document_prefix: str = ""  # text in the field before dictation started

        # Ordered injection: tracks which chunk index to inject next
        self._next_inject_index: int = 0
        self._chunk_counter: int = 0
        self._finalized: dict[int, dict | str] = {}  # index → result (ready to inject)
        self._inject_event = asyncio.Event()

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
        self._tasks = []
        # Try to read existing text from the focused field (best-effort)
        try:
            prefix = read_focused_text()
            if prefix:
                self._document_prefix = prefix
                print(f"[pipeline] Read {len(prefix)} chars of document context")
        except Exception as e:
            print(f"[pipeline] Context read failed (non-fatal): {e}")

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

    @staticmethod
    def _is_silence_hallucination(text: str) -> bool:
        normalized = re.sub(r"[^\w\s]", " ", text.lower(), flags=re.UNICODE)
        normalized = " ".join(normalized.split())
        if not normalized:
            return True
        if normalized in SILENCE_HALLUCINATION_PATTERNS:
            return True
        # Very short one-token outputs are often silence artifacts.
        tokens = normalized.split()
        return len(tokens) == 1 and len(tokens[0]) <= 2

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
        if self._is_silence_hallucination(draft):
            self._signal_finalized(chunk.index, "")
            return

        chunk.draft_text = draft
        chunk.state = ChunkState.DRAFT
        print(f"[pipeline] Draft chunk {chunk.index}: {draft[:80]!r}")

        if self._on_draft:
            self._on_draft(chunk.id, draft)

        # --- 2nd pass: Mistral LLM ---
        chunk.state = ChunkState.REFINING
        try:
            # Build document context: prefix (pre-existing text) + session buffer
            doc_tail = (self._document_prefix[-500:] + self._session_buffer)[-1000:]
            result = await api_client.refine(
                draft, api_key, self._session_context, self._settings, mode,
                injected_tail=doc_tail[-200:],
            )
            final_text = result.get("full_text") or draft
        except Exception as e:
            print(f"[pipeline] Refinement error for chunk {chunk.id}: {e}")
            final_text = draft  # graceful degradation
            result = {"segments": [{"type": "text", "content": draft, "command": "none"}],
                      "full_text": draft, "detected_language": "fr"}

        # Check for control commands (e.g., stop_dictation)
        commands = self._settings.get("dictation_commands") or []
        cmd_lookup = {cmd["id"]: cmd for cmd in commands}
        stop_requested = False
        for seg in result.get("segments", []):
            cmd_def = cmd_lookup.get(seg.get("command", "none"))
            if cmd_def and cmd_def.get("category") == "control":
                action = cmd_def.get("action", {})
                if action.get("control") == "stop_dictation":
                    stop_requested = True

        chunk.final_text = final_text
        chunk.state = ChunkState.FINAL

        if self._on_final:
            self._on_final(chunk.id, final_text)

        # Update rolling context
        if final_text.strip():
            self._session_context.append(final_text.strip())
            if len(self._session_context) > 5:
                self._session_context.pop(0)

        # Signal injection worker with full result for command dispatch
        self._signal_finalized(chunk.index, result)

        if stop_requested:
            self.stop_session()

    def _signal_finalized(self, index: int, result: dict | str):
        """Mark a chunk as ready for ordered injection."""
        self._finalized[index] = result
        # Wake up the injection worker
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

        elif edit_type == "correct_word":
            # content should contain the word to correct; the LLM's full_text
            # should contain the corrected version — but for segment-based dispatch,
            # we need the replacement from the next text segment or from content itself
            word = content.strip()
            if not word:
                print("[pipeline] Correct word: no word specified")
                return
            result = TextFieldEditor.find_word_offset(self._session_buffer, word)
            if result:
                offset_from_end, length = result
                # For now, just delete the word — the LLM's full_text handles replacement
                print(f"[pipeline] Found '{word}' at offset {offset_from_end} from end")
                # Select from current position back to the word, then the word itself
                await loop.run_in_executor(
                    None, self._editor.delete_backwards, offset_from_end
                )
                # Re-inject everything after the deleted word
                remaining = self._session_buffer[:-offset_from_end] if offset_from_end > 0 else ""
                self._session_buffer = remaining
            else:
                print(f"[pipeline] Word '{word}' not found in session buffer")

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

        # Check if any segment is an editing or llm_instructed command
        has_special_commands = any(
            cmd_lookup.get(seg.get("command", "none"), {}).get("category")
            in ("editing", "llm_instructed")
            for seg in segments
        )

        if not has_special_commands:
            # Standard case: use full_text directly (LLM already applied formatting commands)
            full_text = result.get("full_text", "")
            await self._inject_with_spacing(full_text, idx)
            return

        # Special commands present — process segments individually
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
                pass  # handled in _process_chunk

            elif category == "editing":
                await self._handle_editing_command(cmd_id, content, action, idx)

            elif category == "llm_instructed":
                # The LLM should have applied the instruction in full_text
                # Use full_text for the whole result instead of segment-by-segment
                full_text = result.get("full_text", "")
                if full_text:
                    await self._inject_with_spacing(full_text, idx)
                break  # full_text covers the entire result

    async def _injection_worker(self):
        """
        Drains _finalized in strict index order, injecting text into the
        focused app. Runs until the pipeline is stopped and all chunks
        have been processed.
        """
        while True:
            # Inject everything that's ready in order
            while self._next_inject_index in self._finalized:
                result = self._finalized.pop(self._next_inject_index)
                idx = self._next_inject_index
                self._next_inject_index += 1
                await self._dispatch_result(result, idx)

            # Check if session is done and all chunks injected
            if not self._active and self._next_inject_index >= self._chunk_counter:
                break

            # Wait for the next chunk to be finalized
            self._inject_event.clear()
            try:
                await asyncio.wait_for(self._inject_event.wait(), timeout=30.0)
            except TimeoutError:
                break
