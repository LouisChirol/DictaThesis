/**
 * Bridges Electron IPC channels (renderer <-> main) to the Python sidecar.
 */

import { ipcMain, dialog, BrowserWindow, clipboard } from "electron";
import * as fs from "fs";
import { uiLocaleFromSetting } from "../i18n";
import { SidecarManager } from "./sidecar";
import { TrayManager } from "./tray";

function applyWindowTheme(hudWindow: BrowserWindow, settingsWindow: BrowserWindow, theme: string) {
  const bg = theme === "parchment" ? "#f4f0e8" : "#1e1e2e";
  hudWindow.setBackgroundColor(bg);
  settingsWindow.setBackgroundColor(bg);
}

export function setupIpcHandlers(
  sidecar: SidecarManager,
  hudWindow: BrowserWindow,
  settingsWindow: BrowserWindow,
  tray: TrayManager,
  openSettings: () => void,
  quitApp: () => void,
): void {
  function broadcastLocale(language: string | undefined | null): void {
    const locale = uiLocaleFromSetting(language);
    tray.setLocale(language);
    hudWindow.webContents.send("event:locale", locale);
    settingsWindow.webContents.send("event:locale", locale);
  }
  // ── Commands from renderer → sidecar ──

  ipcMain.on("cmd:start_dictation", () => {
    console.log("[ipc] start_dictation");
    sidecar.send({ cmd: "start_dictation" });
  });

  ipcMain.on("cmd:stop_dictation", () => {
    console.log("[ipc] stop_dictation");
    sidecar.send({ cmd: "stop_dictation" });
  });

  ipcMain.on("cmd:update_settings", (_e, data) => {
    sidecar.send({ cmd: "update_settings", data });
  });

  ipcMain.on("cmd:get_settings", () => {
    sidecar.send({ cmd: "get_settings" });
  });

  ipcMain.on("cmd:clear_session", () => {
    sidecar.send({ cmd: "clear_session" });
  });

  ipcMain.on("cmd:quit", () => {
    console.log("[ipc] quit");
    quitApp();
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

  // ── Pin toggle (always-on-top) ──

  ipcMain.handle("window:toggle-pin", () => {
    const pinned = !hudWindow.isAlwaysOnTop();
    hudWindow.setAlwaysOnTop(pinned, "floating");
    return pinned;
  });

  ipcMain.handle("window:ensure-pinned", () => {
    hudWindow.setAlwaysOnTop(true, "floating");
    return hudWindow.isAlwaysOnTop();
  });

  ipcMain.handle("window:is-pinned", () => hudWindow.isAlwaysOnTop());
  ipcMain.handle("clipboard:copy-text", (_e, text: string) => {
    try {
      clipboard.writeText(text ?? "");
      return true;
    } catch {
      return false;
    }
  });

  // ── Window drag (fallback for Linux/WSL2) ──

  let dragStart: { x: number; y: number } | null = null;

  ipcMain.on("window:start-drag", (_e, mouseX: number, mouseY: number) => {
    const [winX, winY] = hudWindow.getPosition();
    dragStart = { x: mouseX - winX, y: mouseY - winY };
  });

  ipcMain.on("window:dragging", (_e, mouseX: number, mouseY: number) => {
    if (dragStart) {
      hudWindow.setPosition(mouseX - dragStart.x, mouseY - dragStart.y);
    }
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
    hudWindow.webContents.send("event:settings", data);
    const theme = data?.data?.ui_theme;
    if (theme) {
      applyWindowTheme(hudWindow, settingsWindow, theme);
    }
    broadcastLocale(data?.data?.language);
  });

  sidecar.on("session_cleared", () => {
    hudWindow.webContents.send("event:session_cleared");
  });

  sidecar.on("error", (data) => {
    hudWindow.webContents.send("event:error", data);
  });
}
