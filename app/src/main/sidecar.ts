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
  private rl: readline.Interface | null = null;
  private restartCount = 0;
  private maxRestarts = 3;
  private shuttingDown = false;

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
    // Development: use venv Python from the python/ directory
    const pythonDir = path.join(__dirname, "..", "..", "..", "python");
    const venvPython = process.platform === "win32"
      ? path.join(pythonDir, ".venv", "Scripts", "python.exe")
      : path.join(pythonDir, ".venv", "bin", "python");
    return { python: venvPython, script: path.join(pythonDir, "sidecar.py") };
  }

  start(): void {
    this.shuttingDown = false;
    const { python, script } = this.getSidecarPath();
    const args = script
      ? [script, "--no-hotkey"]
      : ["--no-hotkey"];
    const spawnedAt = Date.now();
    const childEnv = { ...process.env };
    delete childEnv.ELECTRON_RUN_AS_NODE;

    console.log(`[sidecar] Spawning: ${python} ${args.join(" ")}`);

    this.proc = spawn(python, args, {
      stdio: ["pipe", "pipe", "pipe"],
      env: childEnv,
    });

    // Parse stdout as JSONL protocol
    this.rl = readline.createInterface({ input: this.proc.stdout! });
    this.rl.on("line", (line: string) => {
      try {
        const event = JSON.parse(line);
        this.emit(event.event, event);
      } catch (e) {
        console.error("[sidecar] Bad JSON from stdout:", line);
      }
    });
    this.rl.on("close", () => {
      console.error("[sidecar] stdout reader closed");
    });

    // Forward stderr for debugging
    this.proc.stderr?.on("data", (data: Buffer) => {
      process.stderr.write(`[python] ${data.toString()}`);
    });
    this.proc.stdin?.on("close", () => {
      console.error("[sidecar] stdin closed");
    });
    this.proc.stdin?.on("error", (err: Error) => {
      console.error("[sidecar] stdin error:", err.message);
    });

    this.proc.on("exit", (code: number | null) => {
      console.log(`[sidecar] Process exited with code ${code}`);
      this.rl?.close();
      this.rl = null;
      this.proc = null;
      this.emit("exit", code);

      const exitedQuickly = Date.now() - spawnedAt < 5000;
      const unexpectedExit = !this.shuttingDown && code !== null;
      // Auto-restart unexpected exits, including quick code-0 exits.
      if (unexpectedExit && this.restartCount < this.maxRestarts) {
        this.restartCount++;
        console.log(`[sidecar] Restarting (attempt ${this.restartCount}/${this.maxRestarts})...`);
        const delay = exitedQuickly ? 300 : 1000 * this.restartCount;
        setTimeout(() => this.start(), delay);
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
    this.shuttingDown = true;
    this.maxRestarts = 0; // prevent auto-restart
    if (this.proc) {
      // Ask sidecar to quit explicitly before closing stdin.
      if (this.proc.stdin?.writable) {
        this.proc.stdin.write(`${JSON.stringify({ cmd: "quit" })}\n`);
      }
      this.proc.stdin?.end();
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
