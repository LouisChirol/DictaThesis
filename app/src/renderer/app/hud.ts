/**
 * HUD renderer — chunk display, compact status bar, and controls.
 */

interface ChunkData {
  text: string;
  state: "draft" | "final" | "settled";
  element: HTMLElement;
  settleTimer?: ReturnType<typeof setTimeout>;
}

const chunks = new Map<string, ChunkData>();
const chunksContainer = document.getElementById("chunks")!;
const statusBar = document.getElementById("status-bar")!;
const statusDot = document.getElementById("status-dot")!;
const btnStart = document.getElementById("btn-start") as HTMLButtonElement;
const btnStop = document.getElementById("btn-stop") as HTMLButtonElement;
const btnInsert = document.getElementById("btn-insert") as HTMLButtonElement;
const btnClear = document.getElementById("btn-clear") as HTMLButtonElement;
const insertMenu = document.getElementById("insert-menu")!;
const btnInsertMenu = document.getElementById("btn-insert-menu") as HTMLButtonElement;
const insertMenuPanel = document.getElementById("insert-menu-panel")!;
const copyMenu = document.getElementById("copy-menu")!;
const btnCopyMenu = document.getElementById("btn-copy-menu") as HTMLButtonElement;
const copyMenuPanel = document.getElementById("copy-menu-panel")!;
const btnPin = document.getElementById("btn-pin") as HTMLButtonElement;
const btnSettings = document.getElementById("btn-settings") as HTMLButtonElement;
const btnQuit = document.getElementById("btn-quit") as HTMLButtonElement;

let hasChunks = false;
let insertionEnabled = true;
let clearConfirmPending = false;
let lastStatusMessage = "Ready · F9";
let lastStatusKind: "idle" | "recording" | "processing" = "idle";
const selectedChunkIds = new Set<string>();

const CLEAR_CONFIRM_MESSAGE = "Enter to confirm clear";

// ── Window drag ──

const titlebar = document.querySelector(".titlebar") as HTMLElement;
let isDragging = false;

titlebar.addEventListener("mousedown", (e: MouseEvent) => {
  const target = e.target as HTMLElement;
  if (target.closest(".titlebar-buttons")) return;

  isDragging = true;
  window.dictaThesis.startDrag(e.screenX, e.screenY);
});

document.addEventListener("mousemove", (e: MouseEvent) => {
  if (isDragging) {
    window.dictaThesis.dragging(e.screenX, e.screenY);
  }
});

document.addEventListener("mouseup", () => {
  isDragging = false;
});

// ── Dropdown menus ──

function setInsertMenuOpen(open: boolean): void {
  insertMenuPanel.hidden = !open;
  btnInsertMenu.setAttribute("aria-expanded", open ? "true" : "false");
}

function closeInsertMenu(): void {
  setInsertMenuOpen(false);
}

function setCopyMenuOpen(open: boolean): void {
  copyMenuPanel.hidden = !open;
  btnCopyMenu.setAttribute("aria-expanded", open ? "true" : "false");
}

function closeCopyMenu(): void {
  setCopyMenuOpen(false);
}

btnInsertMenu.addEventListener("click", (e) => {
  e.stopPropagation();
  closeCopyMenu();
  setInsertMenuOpen(insertMenuPanel.hidden);
});

btnCopyMenu.addEventListener("click", (e) => {
  e.stopPropagation();
  closeInsertMenu();
  setCopyMenuOpen(copyMenuPanel.hidden);
});

insertMenuPanel.querySelectorAll<HTMLButtonElement>(".insert-menu-item").forEach((btn) => {
  btn.addEventListener("click", () => {
    const text = btn.dataset.insert ?? "";
    if (text) {
      window.dictaThesis.injectLiteral(text);
    }
    closeInsertMenu();
  });
});

copyMenuPanel.querySelectorAll<HTMLButtonElement>(".copy-menu-item").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const action = btn.dataset.copy;
    closeCopyMenu();
    if (action === "selected") {
      const text = getSelectedChunkText();
      if (!text) {
        setStatus("Nothing selected", "idle");
        return;
      }
      await copyText(text);
    } else if (action === "all") {
      const text = getAllChunkText();
      if (!text) {
        setStatus("Nothing to copy", "idle");
        return;
      }
      await copyText(text);
    } else if (action === "unselect") {
      clearSelection();
      setStatus("Selection cleared", "idle");
    }
  });
});

document.addEventListener("click", (e) => {
  if (!insertMenu.contains(e.target as Node)) {
    closeInsertMenu();
  }
  if (!copyMenu.contains(e.target as Node)) {
    closeCopyMenu();
  }
});

// ── Buttons ──

btnStart.addEventListener("click", () => window.dictaThesis.startDictation());
btnStop.addEventListener("click", () => window.dictaThesis.stopDictation());
btnInsert.addEventListener("click", () => {
  insertionEnabled = !insertionEnabled;
  updateInsertButton();
  window.dictaThesis.saveSettings({ enable_injection: insertionEnabled });
});
btnClear.addEventListener("click", () => {
  if (clearConfirmPending) {
    cancelClearConfirm();
  } else {
    startClearConfirm();
  }
});
btnPin.addEventListener("click", async () => {
  const pinned = await window.dictaThesis.togglePin();
  setPinButtonState(pinned);
});
btnSettings.addEventListener("click", () => window.dictaThesis.openSettings());
btnQuit.addEventListener("click", () => window.dictaThesis.quit());

document.addEventListener("keydown", (e: KeyboardEvent) => {
  if (!clearConfirmPending) return;

  if (e.key === "Enter") {
    e.preventDefault();
    confirmClear();
  } else if (e.key === "Escape") {
    e.preventDefault();
    cancelClearConfirm();
  }
});

// ── Chunk rendering ──

function clearHudChunks(): void {
  chunksContainer.innerHTML = "";
  chunks.clear();
  clearSelection();
  hasChunks = false;
  chunksContainer.innerHTML = '<div class="empty-state">Transcribed text appears here</div>';
}

function clearEmptyState(): void {
  if (!hasChunks) {
    chunksContainer.innerHTML = "";
    hasChunks = true;
  }
}

function addOrUpdateChunk(chunkId: string, text: string, state: "draft" | "final"): void {
  clearEmptyState();

  const existing = chunks.get(chunkId);
  if (existing) {
    existing.text = text;
    existing.state = state;
    existing.element.textContent = text;
    existing.element.className = `chunk chunk-${state}`;
    if (selectedChunkIds.has(chunkId)) {
      existing.element.classList.add("selected");
    }

    if (state === "final") {
      existing.settleTimer = setTimeout(() => settleChunk(chunkId), 3000);
    }
  } else {
    const el = document.createElement("div");
    el.className = `chunk chunk-${state}`;
    el.textContent = text;
    el.dataset.chunkId = chunkId;
    el.title = "Click to select for copy";
    el.addEventListener("click", () => toggleChunkSelection(chunkId));
    chunksContainer.appendChild(el);

    const data: ChunkData = { text, state, element: el };
    chunks.set(chunkId, data);

    if (state === "final") {
      data.settleTimer = setTimeout(() => settleChunk(chunkId), 3000);
    }
  }

  chunksContainer.scrollTop = chunksContainer.scrollHeight;
}

function settleChunk(chunkId: string): void {
  const chunk = chunks.get(chunkId);
  if (chunk && chunk.state === "final") {
    chunk.state = "settled";
    chunk.element.className = "chunk chunk-settled";
    if (selectedChunkIds.has(chunkId)) {
      chunk.element.classList.add("selected");
    }
  }
}

function toggleChunkSelection(chunkId: string): void {
  const chunk = chunks.get(chunkId);
  if (!chunk) return;
  if (selectedChunkIds.has(chunkId)) {
    selectedChunkIds.delete(chunkId);
    chunk.element.classList.remove("selected");
  } else {
    selectedChunkIds.add(chunkId);
    chunk.element.classList.add("selected");
  }
}

function clearSelection(): void {
  for (const id of selectedChunkIds) {
    const chunk = chunks.get(id);
    if (chunk) chunk.element.classList.remove("selected");
  }
  selectedChunkIds.clear();
}

function getSelectedChunkText(): string {
  const parts: string[] = [];
  for (const [id, chunk] of chunks.entries()) {
    if (selectedChunkIds.has(id) && chunk.text.trim()) {
      parts.push(chunk.text.trim());
    }
  }
  return parts.join("\n");
}

function getAllChunkText(): string {
  const parts: string[] = [];
  for (const chunk of chunks.values()) {
    if (chunk.text.trim()) {
      parts.push(chunk.text.trim());
    }
  }
  return parts.join("\n");
}

async function copyText(text: string): Promise<void> {
  const ok = await window.dictaThesis.copyText(text);
  setStatus(ok ? "Copied" : "Copy failed", "idle");
}

function setPinButtonState(pinned: boolean): void {
  btnPin.classList.toggle("pinned", pinned);
  btnPin.title = pinned ? "Always on top (pinned)" : "Not pinned";
}

function updateInsertButton(): void {
  btnInsert.classList.toggle("active", insertionEnabled);
  btnInsert.title = insertionEnabled
    ? "Paste into focused app (on)"
    : "Cursor mode — not pasting";
}

function shortenStatus(
  message: string,
  status: "idle" | "recording" | "processing",
): string {
  const lower = message.toLowerCase();
  if (status === "recording") {
    return insertionEnabled ? "Recording…" : "Recording · cursor mode";
  }
  if (status === "processing") {
    return "Finishing…";
  }
  if (lower.includes("ready") || lower.includes("done") || lower.includes("start")) {
    return "Ready · F9";
  }
  if (lower.includes("copied")) return "Copied";
  if (lower.includes("cleared")) return "Cleared";
  if (lower.includes("selection")) return "Selection cleared";
  if (lower.includes("error")) return "Error";
  return message.length > 48 ? `${message.slice(0, 45)}…` : message;
}

function setStatus(
  message: string,
  status: "idle" | "recording" | "processing",
  options?: { remember?: boolean },
): void {
  const display = shortenStatus(message, status);

  if (options?.remember) {
    lastStatusMessage = display;
    lastStatusKind = status;
  }

  statusBar.textContent = display;
  statusBar.className = "titlebar-status";
  statusBar.classList.add(status);

  statusDot.className = `status-dot ${status}`;

  if (clearConfirmPending) {
    statusBar.classList.add("confirm-pending");
    statusBar.textContent = CLEAR_CONFIRM_MESSAGE;
  }
}

function setRecordingUI(recording: boolean): void {
  btnStart.disabled = recording;
  btnStop.disabled = !recording;
}

function startClearConfirm(): void {
  clearConfirmPending = true;
  btnClear.classList.add("btn-clear-pending");
  btnClear.textContent = "Confirm";
  setStatus(CLEAR_CONFIRM_MESSAGE, lastStatusKind);
  statusBar.classList.add("confirm-pending");
}

function cancelClearConfirm(): void {
  if (!clearConfirmPending) return;
  clearConfirmPending = false;
  btnClear.classList.remove("btn-clear-pending");
  btnClear.textContent = "Clear";
  setStatus(lastStatusMessage, lastStatusKind);
}

function confirmClear(): void {
  clearConfirmPending = false;
  btnClear.classList.remove("btn-clear-pending");
  btnClear.textContent = "Clear";
  window.dictaThesis.clearSession();
  clearHudChunks();
  setStatus("Cleared", "idle", { remember: true });
}

// ── Event handlers ──

window.dictaThesis.onChunkUpdate((data) => {
  addOrUpdateChunk(data.chunk_id, data.text, data.state);
});

window.dictaThesis.onStatusChange((data) => {
  cancelClearConfirm();
  setStatus(data.message, data.status, { remember: true });

  if (data.status === "recording") {
    setRecordingUI(true);
    clearHudChunks();
  } else if (data.status === "idle") {
    setRecordingUI(false);
  }
});

window.dictaThesis.onError((data) => {
  cancelClearConfirm();
  setStatus(data.message, "idle", { remember: true });
  setRecordingUI(false);

  clearEmptyState();
  const el = document.createElement("div");
  el.className = "chunk chunk-error";
  el.textContent = data.message;
  chunksContainer.appendChild(el);
  chunksContainer.scrollTop = chunksContainer.scrollHeight;
});

window.dictaThesis.onSessionCleared(() => {
  clearHudChunks();
});

window.dictaThesis.onSettings((data) => {
  insertionEnabled = data.data.enable_injection ?? true;
  updateInsertButton();
  applyUiTheme(data.data.ui_theme);
});

window.dictaThesis.getSettings();
window.dictaThesis.isPinned().then(setPinButtonState).catch(() => setPinButtonState(true));
