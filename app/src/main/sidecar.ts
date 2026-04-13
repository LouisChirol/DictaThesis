/**
 * Manages the Python sidecar process.
 * Spawns Python, communicates via JSONL over stdin/stdout.
 */

import { spawn, ChildProcess } from "child_process";
import { EventEmitter } from "events";
import * as readline from "readline";
import * as path from "path";
import { app } from "electron";

export interface SidecarCommand {
  cmd: string;
  data?: Record<string, unknown>;
}

export class SidecarManager extends EventEmitter {
  private proc: ChildProcess | null = null;
  private restartCount = 0;
  private maxRestarts = 3;

  /**
   * Resolve the path to the Python sidecar script or executable.
   */
  private getSidecarPath(): { python: string; script: string } {
    if (app.isPackaged) {
      // Production: PyInstaller bundle alongside Electron resources
      const sidecarDir = path.join(process.resourcesPath, "sidecar");
      const ext = process.platform === "win32" ? ".exe" : "";
      return { python: path.join(sidecarDir, `sidecar${ext}`), script: "" };
    }
    // Development: run from source
    const pythonDir = path.join(__dirname, "..", "..", "..", "python");
    return { python: "python3", script: path.join(pythonDir, "sidecar.py") };
  }

  start(): void {
    const { python, script } = this.getSidecarPath();
    const args = script
      ? [script, "--no-hotkey"]
      : ["--no-hotkey"];

    console.log(`[sidecar] Spawning: ${python} ${args.join(" ")}`);

    this.proc = spawn(python, args, {
      stdio: ["pipe", "pipe", "pipe"],
      env: { ...process.env, ELECTRON_RUN_AS_NODE: undefined },
    });

    // Parse stdout as JSONL protocol
    const rl = readline.createInterface({ input: this.proc.stdout! });
    rl.on("line", (line: string) => {
      try {
        const event = JSON.parse(line);
        this.emit(event.event, event);
      } catch (e) {
        console.error("[sidecar] Bad JSON from stdout:", line);
      }
    });

    // Forward stderr for debugging
    this.proc.stderr?.on("data", (data: Buffer) => {
      process.stderr.write(`[python] ${data.toString()}`);
    });

    this.proc.on("exit", (code: number | null) => {
      console.log(`[sidecar] Process exited with code ${code}`);
      this.proc = null;
      this.emit("exit", code);

      // Auto-restart on unexpected crash
      if (code !== 0 && code !== null && this.restartCount < this.maxRestarts) {
        this.restartCount++;
        console.log(`[sidecar] Restarting (attempt ${this.restartCount}/${this.maxRestarts})...`);
        setTimeout(() => this.start(), 1000 * this.restartCount);
      }
    });

    this.proc.on("error", (err: Error) => {
      console.error("[sidecar] Spawn error:", err.message);
      this.emit("spawn_error", err);
    });
  }

  send(command: SidecarCommand): void {
    if (!this.proc?.stdin?.writable) {
      console.error("[sidecar] Cannot send — process not running");
      return;
    }
    const line = JSON.stringify(command) + "\n";
    this.proc.stdin.write(line);
  }

  kill(): void {
    this.maxRestarts = 0; // prevent auto-restart
    if (this.proc) {
      this.proc.stdin?.end(); // close stdin → Python detects EOF and exits
      // Force kill after 3 seconds if it hasn't exited
      const p = this.proc;
      setTimeout(() => {
        if (p && !p.killed) {
          p.kill("SIGKILL");
        }
      }, 3000);
    }
  }
}
