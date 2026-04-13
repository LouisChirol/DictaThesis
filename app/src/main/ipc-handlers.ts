/**
 * Bridges Electron IPC channels (renderer <-> main) to the Python sidecar.
 */

import { ipcMain, dialog, BrowserWindow } from "electron";
import * as fs from "fs";
import { SidecarManager } from "./sidecar";

export function setupIpcHandlers(
  sidecar: SidecarManager,
  hudWindow: BrowserWindow,
  settingsWindow: BrowserWindow,
  openSettings: () => void,
): void {
  // ── Commands from renderer → sidecar ──

  ipcMain.on("cmd:start_dictation", () => {
    sidecar.send({ cmd: "start_dictation" });
  });

  ipcMain.on("cmd:stop_dictation", () => {
    sidecar.send({ cmd: "stop_dictation" });
  });

  ipcMain.on("cmd:update_settings", (_e, data) => {
    sidecar.send({ cmd: "update_settings", data });
  });

  ipcMain.on("cmd:get_settings", () => {
    sidecar.send({ cmd: "get_settings" });
  });

  ipcMain.on("cmd:quit", () => {
    sidecar.send({ cmd: "quit" });
  });

  // ── UI actions ──

  ipcMain.on("ui:open_settings", () => {
    openSettings();
  });

  ipcMain.handle("ui:load_bib_file", async () => {
    const result = await dialog.showOpenDialog({
      filters: [{ name: "BibTeX", extensions: ["bib"] }],
      properties: ["openFile"],
    });
    if (!result.canceled && result.filePaths[0]) {
      try {
        return fs.readFileSync(result.filePaths[0], "utf-8");
      } catch {
        return null;
      }
    }
    return null;
  });

  // ── Events from sidecar → renderer windows ──

  sidecar.on("chunk_update", (data) => {
    hudWindow.webContents.send("event:chunk_update", data);
  });

  sidecar.on("status_change", (data) => {
    hudWindow.webContents.send("event:status_change", data);
  });

  sidecar.on("settings", (data) => {
    settingsWindow.webContents.send("event:settings", data);
  });

  sidecar.on("error", (data) => {
    hudWindow.webContents.send("event:error", data);
  });
}
