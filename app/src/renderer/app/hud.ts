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
const btnPin = document.getElementById("btn-pin") as HTMLButtonElement;
const btnSettings = document.getElementById("btn-settings") as HTMLButtonElement;
const btnQuit = document.getElementById("btn-quit") as HTMLButtonElement;

let hasChunks = false;

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
btnPin.addEventListener("click", async () => {
  const pinned = await window.dictaThesis.togglePin();
  btnPin.classList.toggle("pinned", pinned);
  btnPin.title = pinned ? "Always on top (pinned)" : "Not pinned";
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

    // If finalized, schedule settle
    if (state === "final") {
      existing.settleTimer = setTimeout(() => settleChunk(chunkId), 3000);
    }
  } else {
    // Create new chunk element
    const el = document.createElement("div");
    el.className = `chunk chunk-${state}`;
    el.textContent = text;
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
  }
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
