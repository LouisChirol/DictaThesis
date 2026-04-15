/**
 * HUD renderer — handles chunk display, status updates, and button interactions.
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
const btnStart = document.getElementById("btn-start") as HTMLButtonElement;
const btnStop = document.getElementById("btn-stop") as HTMLButtonElement;
const btnInsert = document.getElementById("btn-insert") as HTMLButtonElement;
const btnCopySelected = document.getElementById("btn-copy-selected") as HTMLButtonElement;
const btnCopyAll = document.getElementById("btn-copy-all") as HTMLButtonElement;
const btnUnselectAll = document.getElementById("btn-unselect-all") as HTMLButtonElement;
const btnPin = document.getElementById("btn-pin") as HTMLButtonElement;
const btnSettings = document.getElementById("btn-settings") as HTMLButtonElement;
const btnQuit = document.getElementById("btn-quit") as HTMLButtonElement;

let hasChunks = false;
let insertionEnabled = true;
const selectedChunkIds = new Set<string>();

// ── Window drag (JS fallback — -webkit-app-region: drag is broken on Linux/WSL) ──

const titlebar = document.querySelector(".titlebar") as HTMLElement;
let isDragging = false;

titlebar.addEventListener("mousedown", (e: MouseEvent) => {
  // Only drag from the titlebar itself, not from buttons
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

// ── Buttons ──

btnStart.addEventListener("click", () => window.dictaThesis.startDictation());
btnStop.addEventListener("click", () => window.dictaThesis.stopDictation());
btnInsert.addEventListener("click", () => {
  insertionEnabled = !insertionEnabled;
  updateInsertButton();
  window.dictaThesis.saveSettings({ enable_injection: insertionEnabled });
});
btnCopySelected.addEventListener("click", async () => {
  const selectedText = getSelectedChunkText();
  if (!selectedText) {
    setStatus("No chunk selected", "idle");
    return;
  }
  await copyText(selectedText);
});
btnCopyAll.addEventListener("click", async () => {
  const text = getAllChunkText();
  if (!text) {
    setStatus("No text to copy yet", "idle");
    return;
  }
  await copyText(text);
});
btnUnselectAll.addEventListener("click", () => {
  clearSelection();
  setStatus("Selection cleared", "idle");
});
btnPin.addEventListener("click", async () => {
  const pinned = await window.dictaThesis.togglePin();
  setPinButtonState(pinned);
});
btnSettings.addEventListener("click", () => window.dictaThesis.openSettings());
btnQuit.addEventListener("click", () => window.dictaThesis.quit());

// ── Chunk rendering ──

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
    // Update existing chunk
    existing.text = text;
    existing.state = state;
    existing.element.textContent = text;
    existing.element.className = `chunk chunk-${state}`;
    if (selectedChunkIds.has(chunkId)) {
      existing.element.classList.add("selected");
    }

    // If finalized, schedule settle
    if (state === "final") {
      existing.settleTimer = setTimeout(() => settleChunk(chunkId), 3000);
    }
  } else {
    // Create new chunk element
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

  // Auto-scroll to bottom
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
  setStatus(ok ? "Copied to clipboard" : "Copy failed", "idle");
}

function setPinButtonState(pinned: boolean): void {
  btnPin.classList.toggle("pinned", pinned);
  btnPin.title = pinned ? "Always on top (pinned)" : "Not pinned";
}

function updateInsertButton(): void {
  btnInsert.classList.toggle("active", insertionEnabled);
  btnInsert.textContent = insertionEnabled ? "Insert ON" : "Insert OFF";
  btnInsert.title = insertionEnabled
    ? "Cursor insertion enabled"
    : "Cursor insertion disabled";
}

function setStatus(message: string, status: "idle" | "recording" | "processing"): void {
  statusBar.textContent = message;
  statusBar.className = `status-bar ${status}`;
}

function setRecordingUI(recording: boolean): void {
  btnStart.disabled = recording;
  btnStop.disabled = !recording;
}

// ── Event handlers ──

window.dictaThesis.onChunkUpdate((data) => {
  addOrUpdateChunk(data.chunk_id, data.text, data.state);
});

window.dictaThesis.onStatusChange((data) => {
  setStatus(data.message, data.status);

  if (data.status === "recording") {
    setRecordingUI(true);
    // Clear previous chunks on new session
    chunksContainer.innerHTML = "";
    chunks.clear();
    clearSelection();
    hasChunks = false;
    clearEmptyState();
  } else if (data.status === "idle") {
    setRecordingUI(false);
  }
  // "processing" keeps stop disabled since we already stopped
});

window.dictaThesis.onError((data) => {
  setStatus("Error", "idle");
  setRecordingUI(false);

  // Show error in chunks area for visibility
  clearEmptyState();
  const el = document.createElement("div");
  el.className = "chunk chunk-error";
  el.textContent = data.message;
  chunksContainer.appendChild(el);
  chunksContainer.scrollTop = chunksContainer.scrollHeight;
});

window.dictaThesis.onSettings((data) => {
  insertionEnabled = data.data.enable_injection ?? true;
  updateInsertButton();
});

window.dictaThesis.getSettings();
window.dictaThesis.isPinned().then(setPinButtonState).catch(() => setPinButtonState(true));
