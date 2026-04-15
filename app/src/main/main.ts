/**
 * DictaThesis — Electron main process.
 * Creates the HUD overlay, settings window, tray icon, and manages the Python sidecar.
 */

import { app, BrowserWindow, globalShortcut } from "electron";
import * as path from "path";
import * as fs from "fs";
import { SidecarManager } from "./sidecar";
import { TrayManager } from "./tray";
import { registerShortcut, unregisterAll } from "./shortcuts";
import { setupIpcHandlers } from "./ipc-handlers";

// Disable GPU acceleration on WSL2 to avoid GPU process crashes
function isWSL(): boolean {
  try {
    const version = fs.readFileSync("/proc/version", "utf-8");
    return version.toLowerCase().includes("microsoft");
  } catch {
    return false;
  }
}
if (isWSL()) {
  app.disableHardwareAcceleration();
  app.commandLine.appendSwitch("disable-gpu");
  app.commandLine.appendSwitch("disable-software-rasterizer");
  app.commandLine.appendSwitch("in-process-gpu");
}

let hudWindow: BrowserWindow;
let settingsWindow: BrowserWindow;
let sidecar: SidecarManager;
let tray: TrayManager;
let isRecording = false;
let appIsQuitting = false;

const preloadPath = path.join(__dirname, "preload.js");

function createHudWindow(): BrowserWindow {
  const win = new BrowserWindow({
    width: 420,
    height: 280,
    frame: false,
    transparent: false, // solid bg for cross-platform compat (WSL2/Wayland)
    alwaysOnTop: true,
    resizable: true,
    skipTaskbar: true,
    minimizable: false,
    backgroundColor: "#1e1e2e",
    webPreferences: {
      preload: preloadPath,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false, // required for preload require() to work
    },
  });

  win.loadFile(path.join(__dirname, "..", "renderer", "index.html"));
  win.setAlwaysOnTop(true, "floating");

  // Prevent window from being closed — hide instead (quit via tray or button)
  win.on("close", (e) => {
    if (!appIsQuitting) {
      e.preventDefault();
      win.hide();
    }
  });

  return win;
}

function createSettingsWindow(): BrowserWindow {
  const win = new BrowserWindow({
    width: 560,
    height: 680,
    show: false,
    frame: true,
    resizable: true,
    backgroundColor: "#1e1e2e",
    webPreferences: {
      preload: preloadPath,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  win.setMenuBarVisibility(false);
  win.loadFile(path.join(__dirname, "..", "renderer", "settings.html"));

  // Hide instead of destroy
  win.on("close", (e) => {
    if (!appIsQuitting) {
      e.preventDefault();
      win.hide();
    }
  });

  return win;
}

function openSettings(): void {
  sidecar.send({ cmd: "get_settings" });
  settingsWindow.show();
  settingsWindow.focus();
}

function toggleDictation(): void {
  if (isRecording) {
    sidecar.send({ cmd: "stop_dictation" });
  } else {
    sidecar.send({ cmd: "start_dictation" });
  }
}

function quit(): void {
  appIsQuitting = true;
  unregisterAll();
  tray?.destroy();
  sidecar?.kill();
  app.quit();
}

app.whenReady().then(() => {
  // Create windows
  hudWindow = createHudWindow();
  settingsWindow = createSettingsWindow();

  // Start Python sidecar
  sidecar = new SidecarManager();
  sidecar.start();

  // Set up IPC bridge
  setupIpcHandlers(sidecar, hudWindow, settingsWindow, openSettings, quit);

  // System tray
  tray = new TrayManager(sidecar, hudWindow, openSettings, quit);
  tray.start();

  // Global shortcut (default F9)
  registerShortcut("f9", toggleDictation);

  // React to sidecar events for tray state
  sidecar.on("status_change", (data: { status: string }) => {
    const status = data.status as "idle" | "recording" | "processing";
    isRecording = status === "recording";
    tray.setStatus(status);
  });

  sidecar.on("ready", () => {
    console.log("[main] Sidecar is ready");
  });

  sidecar.on("spawn_error", (err: Error) => {
    console.error("[main] Failed to start sidecar:", err.message);
    hudWindow.webContents.send("event:error", {
      event: "error",
      message: `Failed to start Python backend: ${err.message}`,
    });
  });
});

// macOS: re-show window when clicking dock icon
app.on("activate", () => {
  if (hudWindow && !hudWindow.isVisible()) {
    hudWindow.show();
  }
});

// Clean up on quit
app.on("before-quit", () => {
  appIsQuitting = true;
  unregisterAll();
  sidecar?.kill();
  tray?.destroy();
});

app.on("window-all-closed", () => {
  // Don't quit — we live in the tray
});
