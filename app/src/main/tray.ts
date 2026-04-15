/**
 * System tray icon with state-based icons and context menu.
 */

import { Tray, Menu, nativeImage, BrowserWindow } from "electron";
import * as path from "path";
import { SidecarManager } from "./sidecar";

type AppStatus = "idle" | "recording" | "processing";

export class TrayManager {
  private tray: Tray | null = null;
  private status: AppStatus = "idle";
  private icons: Record<AppStatus, Electron.NativeImage>;

  constructor(
    private sidecar: SidecarManager,
    private hudWindow: BrowserWindow,
    private onOpenSettings: () => void,
    private onQuit: () => void,
  ) {
    this.icons = {
      idle: this.createIcon("#6c7086", "#89b4fa"),       // gray body, blue accent
      recording: this.createIcon("#f38ba8", "#f38ba8"),  // red
      processing: this.createIcon("#6c7086", "#fab387"), // gray body, orange accent
    };
  }

  /**
   * Create a 22x22 tray icon as a filled circle with an accent dot.
   */
  private createIcon(bodyColor: string, accentColor: string): Electron.NativeImage {
    const size = 22;
    const svg = `
      <svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
        <circle cx="11" cy="11" r="10" fill="${bodyColor}" />
        <circle cx="11" cy="11" r="4" fill="${accentColor}" />
      </svg>
    `.trim();
    return nativeImage.createFromBuffer(Buffer.from(svg), {
      width: size,
      height: size,
      scaleFactor: 1.0,
    });
  }

  start(): void {
    this.tray = new Tray(this.icons.idle);
    this.tray.setToolTip("DictaThesis");
    this.updateMenu();

    this.tray.on("click", () => {
      if (this.hudWindow.isVisible()) {
        this.hudWindow.focus();
      } else {
        this.hudWindow.show();
      }
    });
  }

  setStatus(status: AppStatus): void {
    this.status = status;
    if (this.tray) {
      this.tray.setImage(this.icons[status]);
      this.updateMenu();
    }
  }

  private updateMenu(): void {
    if (!this.tray) return;

    const isRecording = this.status === "recording";
    const menu = Menu.buildFromTemplate([
      {
        label: isRecording ? "Stop Dictation" : "Start Dictation",
        click: () => {
          this.sidecar.send({
            cmd: isRecording ? "stop_dictation" : "start_dictation",
          });
        },
      },
      { type: "separator" },
      {
        label: "Settings",
        click: () => this.onOpenSettings(),
      },
      { type: "separator" },
      {
        label: "Quit",
        click: () => this.onQuit(),
      },
    ]);
    this.tray.setContextMenu(menu);
  }

  destroy(): void {
    if (this.tray) {
      this.tray.destroy();
      this.tray = null;
    }
  }
}
