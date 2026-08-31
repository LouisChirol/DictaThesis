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
const copyMenu = document.getElementById("copy-menu")!;
const btnCopyMenu = document.getElementById("btn-copy-menu") as HTMLButtonElement;
const copyMenuPanel = document.getElementById("copy-menu-panel")!;
const btnPin = document.getElementById("btn-pin") as HTMLButtonElement;
const btnSettings = document.getElementById("btn-settings") as HTMLButtonElement;
const btnQuit = document.getElementById("btn-quit") as HTMLButtonElement;

let hasChunks = false;
let insertionEnabled = true;
let clearConfirmPending = false;
let lastStatusMessage = "";
let lastStatusKind: "idle" | "recording" | "processing" = "idle";
const selectedChunkIds = new Set<string>();

function tr(key: string): string {
  return window.i18n.t(key);
}

function refreshHudLabels(): void {
  applyHudI18n();
  updateInsertButton();
  const pinned = btnPin.classList.contains("pinned");
  btnPin.title = tr(pinned ? "hud.pin.pinned" : "hud.pin.unpinned");
  if (clearConfirmPending) {
    btnClear.classList.add("btn-clear-pending");
    btnClear.title = tr("status.clearConfirm");
    const glyph = btnClear.querySelector(".btn-glyph");
    if (glyph) glyph.textContent = "✓";
    statusBar.textContent = tr("status.clearConfirm");
  } else if (lastStatusMessage) {
    statusBar.textContent = lastStatusMessage;
  }
}

// ── Window drag ──

const dragHandles = document.querySelectorAll<HTMLElement>(".window-drag");
let isDragging = false;

dragHandles.forEach((handle) => {
  handle.addEventListener("mousedown", (e: MouseEvent) => {
    const target = e.target as HTMLElement;
    if (target.closest(".titlebar-buttons, .hud-brand-actions")) return;

    isDragging = true;
    window.dictaThesis.startDrag(e.screenX, e.screenY);
  });
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

const DROPDOWN_MARGIN = 6;
const DROPDOWN_GAP = 6;

function positionDropdownPanel(panel: HTMLElement, anchor: HTMLElement): void {
  panel.classList.add("dropdown-panel--fixed");
  panel.hidden = false;

  // Measure at natural size before clamping position
  panel.style.visibility = "hidden";
  panel.style.left = "0";
  panel.style.top = "0";

  const anchorRect = anchor.getBoundingClientRect();
  const panelRect = panel.getBoundingClientRect();
  const maxTop = window.innerHeight - DROPDOWN_MARGIN - panelRect.height;
  const minTop = DROPDOWN_MARGIN;

  let left = anchorRect.right + DROPDOWN_GAP;
  if (left + panelRect.width > window.innerWidth - DROPDOWN_MARGIN) {
    left = anchorRect.left - panelRect.width - DROPDOWN_GAP;
  }
  left = Math.max(DROPDOWN_MARGIN, Math.min(left, window.innerWidth - panelRect.width - DROPDOWN_MARGIN));

  let top = anchorRect.top;
  if (top > maxTop) top = maxTop;
  if (top < minTop) top = minTop;

  panel.style.left = `${left}px`;
  panel.style.top = `${top}px`;
  panel.style.visibility = "";
}

function resetDropdownPanel(panel: HTMLElement): void {
  panel.classList.remove("dropdown-panel--fixed");
  panel.style.left = "";
  panel.style.top = "";
  panel.style.visibility = "";
}

function setCopyMenuOpen(open: boolean): void {
  if (open) {
    positionDropdownPanel(copyMenuPanel, btnCopyMenu);
    btnCopyMenu.setAttribute("aria-expanded", "true");
  } else {
    copyMenuPanel.hidden = true;
    resetDropdownPanel(copyMenuPanel);
    btnCopyMenu.setAttribute("aria-expanded", "false");
  }
}

function closeCopyMenu(): void {
  setCopyMenuOpen(false);
}

function repositionOpenMenus(): void {
  if (!copyMenuPanel.hidden) {
    positionDropdownPanel(copyMenuPanel, btnCopyMenu);
  }
}

window.addEventListener("resize", repositionOpenMenus);

btnCopyMenu.addEventListener("click", (e) => {
  e.stopPropagation();
  if (copyMenuPanel.hidden) {
    btnCopyMenu.scrollIntoView({ block: "nearest" });
    setCopyMenuOpen(true);
  } else {
    closeCopyMenu();
  }
});

copyMenuPanel.querySelectorAll<HTMLButtonElement>(".copy-menu-item").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const action = btn.dataset.copy;
    closeCopyMenu();
    if (action === "selected") {
      const text = getSelectedChunkText();
      if (!text) {
        setStatus(tr("status.nothingSelected"), "idle");
        return;
      }
      await copyText(text);
    } else if (action === "all") {
      const text = getAllChunkText();
      if (!text) {
        setStatus(tr("status.nothingToCopy"), "idle");
        return;
      }
      await copyText(text);
    } else if (action === "unselect") {
      clearSelection();
      setStatus(tr("status.selectionCleared"), "idle");
    }
  });
});

document.addEventListener("click", (e) => {
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
  chunksContainer.innerHTML = `<div class="empty-state" data-i18n="hud.empty">${tr("hud.empty")}</div>`;
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
    el.title = tr("hud.chunkSelect");
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
  setStatus(ok ? tr("status.copied") : tr("status.copyFailed"), "idle");
}

function setPinButtonState(pinned: boolean): void {
  btnPin.classList.toggle("pinned", pinned);
  btnPin.title = tr(pinned ? "hud.pin.pinned" : "hud.pin.unpinned");
}

function updateInsertButton(): void {
  btnInsert.classList.toggle("active", insertionEnabled);
  btnInsert.title = tr(
    insertionEnabled ? "hud.insert.title.on" : "hud.insert.title.off",
  );
}

function shortenStatus(
  message: string,
  status: "idle" | "recording" | "processing",
): string {
  if (status === "recording") {
    return tr(insertionEnabled ? "status.recording" : "status.recording.cursor");
  }
  if (status === "processing") {
    return tr("status.finishing");
  }
  const localized = window.i18n.localizeMessage(message);
  const lower = localized.toLowerCase();
  if (lower.includes("ready") || lower.includes("prêt") || lower.includes("done")) {
    return tr("status.ready");
  }
  if (lower.includes("copié") || lower.includes("copied")) return tr("status.copied");
  if (lower.includes("effacé") || lower.includes("cleared")) return tr("status.cleared");
  if (lower.includes("sélection") || lower.includes("selection")) {
    return tr("status.selectionCleared");
  }
  if (lower.includes("error") || lower.includes("erreur")) return tr("status.error");
  return localized.length > 48 ? `${localized.slice(0, 45)}…` : localized;
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
    statusBar.textContent = tr("status.clearConfirm");
  }
}

function setRecordingUI(recording: boolean): void {
  btnStart.disabled = recording;
  btnStop.disabled = !recording;
}

function startClearConfirm(): void {
  clearConfirmPending = true;
  btnClear.classList.add("btn-clear-pending");
  btnClear.title = tr("status.clearConfirm");
  const glyph = btnClear.querySelector(".btn-glyph");
  if (glyph) glyph.textContent = "✓";
  setStatus(tr("status.clearConfirm"), lastStatusKind);
  statusBar.classList.add("confirm-pending");
}

function cancelClearConfirm(): void {
  if (!clearConfirmPending) return;
  clearConfirmPending = false;
  btnClear.classList.remove("btn-clear-pending");
  btnClear.title = tr("hud.clear.title");
  const glyph = btnClear.querySelector(".btn-glyph");
  if (glyph) glyph.textContent = "⌫";
  setStatus(lastStatusMessage, lastStatusKind);
}

function confirmClear(): void {
  clearConfirmPending = false;
  btnClear.classList.remove("btn-clear-pending");
  btnClear.title = tr("hud.clear.title");
  const glyph = btnClear.querySelector(".btn-glyph");
  if (glyph) glyph.textContent = "⌫";
  window.dictaThesis.clearSession();
  clearHudChunks();
  setStatus(tr("status.cleared"), "idle", { remember: true });
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
  const message = window.i18n.localizeMessage(data.message);
  setStatus(message, "idle", { remember: true });
  setRecordingUI(false);

  clearEmptyState();
  const el = document.createElement("div");
  el.className = "chunk chunk-error";
  el.textContent = message;
  chunksContainer.appendChild(el);
  chunksContainer.scrollTop = chunksContainer.scrollHeight;
});

window.dictaThesis.onSessionCleared(() => {
  clearHudChunks();
});

window.dictaThesis.onSettings((data) => {
  insertionEnabled = data.data.enable_injection ?? true;
  window.i18n.setLocale(data.data.language);
  applyUiTheme(data.data.ui_theme);
  refreshHudLabels();
  updateInsertButton();
});

window.i18n.onLocaleChange(() => refreshHudLabels());

window.dictaThesis.getSettings();
setPinButtonState(true);
window.dictaThesis
  .ensurePinned()
  .then(setPinButtonState)
  .catch(() => setPinButtonState(true));
refreshHudLabels();
updateInsertButton();
