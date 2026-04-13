import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("dictaThesis", {
  // Commands
  startDictation: () => ipcRenderer.send("cmd:start_dictation"),
  stopDictation: () => ipcRenderer.send("cmd:stop_dictation"),
  openSettings: () => ipcRenderer.send("ui:open_settings"),
  quit: () => ipcRenderer.send("cmd:quit"),
  saveSettings: (data: Record<string, unknown>) =>
    ipcRenderer.send("cmd:update_settings", data),
  getSettings: () => ipcRenderer.send("cmd:get_settings"),
  loadBibFile: () => ipcRenderer.invoke("ui:load_bib_file"),

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
});
