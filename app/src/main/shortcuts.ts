/**
 * Global shortcut registration.
 * Maps settings key names to Electron accelerator format.
 */

import { globalShortcut } from "electron";

const KEY_MAP: Record<string, string> = {
  f1: "F1", f2: "F2", f3: "F3", f4: "F4",
  f5: "F5", f6: "F6", f7: "F7", f8: "F8",
  f9: "F9", f10: "F10", f11: "F11", f12: "F12",
  scroll_lock: "Scrolllock",
  pause: "Pause",
  insert: "Insert",
  home: "Home",
  end: "End",
  page_up: "PageUp",
  page_down: "PageDown",
};

let currentAccelerator: string | null = null;

export function registerShortcut(keyName: string, callback: () => void): boolean {
  unregisterShortcut();

  const accelerator = KEY_MAP[keyName.toLowerCase()] || keyName.toUpperCase();
  try {
    const ok = globalShortcut.register(accelerator, callback);
    if (ok) {
      currentAccelerator = accelerator;
      console.log(`[shortcuts] Registered global shortcut: ${accelerator}`);
    } else {
      console.error(`[shortcuts] Failed to register: ${accelerator}`);
    }
    return ok;
  } catch (e) {
    console.error(`[shortcuts] Error registering ${accelerator}:`, e);
    return false;
  }
}

export function unregisterShortcut(): void {
  if (currentAccelerator) {
    globalShortcut.unregister(currentAccelerator);
    currentAccelerator = null;
  }
}

export function unregisterAll(): void {
  globalShortcut.unregisterAll();
  currentAccelerator = null;
}
