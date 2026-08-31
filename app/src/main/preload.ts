import { contextBridge, ipcRenderer } from "electron";
import { t, uiLocaleFromSetting, localizeBackendMessage, type MessageKey, type UiLocale } from "../i18n";

let currentLocale: UiLocale = "en";
const localeListeners = new Set<(locale: UiLocale) => void>();

function setLocale(locale: UiLocale): void {
  currentLocale = locale;
  for (const cb of localeListeners) cb(locale);
}

ipcRenderer.on("event:locale", (_e, locale: UiLocale) => {
  setLocale(locale);
});

contextBridge.exposeInMainWorld("i18n", {
  get locale() {
    return currentLocale;
  },
  t: (key: MessageKey) => t(key, currentLocale),
  localizeMessage: (message: string) => localizeBackendMessage(message, currentLocale),
  setLocale: (language: string) => setLocale(uiLocaleFromSetting(language)),
  onLocaleChange: (cb: (locale: UiLocale) => void) => {
    localeListeners.add(cb);
  },
});

contextBridge.exposeInMainWorld("dictaThesis", {
  // Commands
  startDictation: () => ipcRenderer.send("cmd:start_dictation"),
  stopDictation: () => ipcRenderer.send("cmd:stop_dictation"),
  openSettings: () => ipcRenderer.send("ui:open_settings"),
  quit: () => ipcRenderer.send("cmd:quit"),
  saveSettings: (data: Record<string, unknown>) =>
    ipcRenderer.send("cmd:update_settings", data),
  getSettings: () => ipcRenderer.send("cmd:get_settings"),
  clearSession: () => ipcRenderer.send("cmd:clear_session"),
  loadBibFile: () => ipcRenderer.invoke("ui:load_bib_file"),

  // Window controls
  startDrag: (x: number, y: number) => ipcRenderer.send("window:start-drag", x, y),
  dragging: (x: number, y: number) => ipcRenderer.send("window:dragging", x, y),
  togglePin: () => ipcRenderer.invoke("window:toggle-pin"),
  ensurePinned: () => ipcRenderer.invoke("window:ensure-pinned"),
  isPinned: () => ipcRenderer.invoke("window:is-pinned"),
  copyText: (text: string) => ipcRenderer.invoke("clipboard:copy-text", text),

  // Event subscriptions
  onChunkUpdate: (cb: (data: any) => void) => {
    ipcRenderer.on("event:chunk_update", (_e, data) => cb(data));
  },
  onStatusChange: (cb: (data: any) => void) => {
    ipcRenderer.on("event:status_change", (_e, data) => cb(data));
  },
  onSettings: (cb: (data: any) => void) => {
    ipcRenderer.on("event:settings", (_e, data) => cb(data));
  },
  onError: (cb: (data: any) => void) => {
    ipcRenderer.on("event:error", (_e, data) => cb(data));
  },
  onSessionCleared: (cb: () => void) => {
    ipcRenderer.on("event:session_cleared", () => cb());
  },
});
