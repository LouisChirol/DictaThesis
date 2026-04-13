// IPC protocol types shared between main and renderer processes.

export interface ChunkUpdateEvent {
  event: "chunk_update";
  chunk_id: string;
  state: "draft" | "final";
  text: string;
}

export interface StatusChangeEvent {
  event: "status_change";
  status: "idle" | "recording" | "processing";
  message: string;
}

export interface SettingsEvent {
  event: "settings";
  data: SettingsData;
}

export interface ErrorEvent {
  event: "error";
  message: string;
}

export interface ReadyEvent {
  event: "ready";
}

export type SidecarEvent =
  | ChunkUpdateEvent
  | StatusChangeEvent
  | SettingsEvent
  | ErrorEvent
  | ReadyEvent;

export interface SettingsData {
  api_key: string;
  language: "fr" | "en" | "auto";
  mode: "normal" | "equation";
  shortcut_key: string;
  vad_silence_duration: number;
  vad_mode: number;
  vocabulary: string[];
  bibliography: string;
}

// API exposed to renderer via contextBridge
export interface DictaThesisAPI {
  startDictation: () => void;
  stopDictation: () => void;
  openSettings: () => void;
  quit: () => void;
  saveSettings: (data: Partial<SettingsData>) => void;
  getSettings: () => void;
  loadBibFile: () => Promise<string | null>;

  onChunkUpdate: (cb: (data: ChunkUpdateEvent) => void) => void;
  onStatusChange: (cb: (data: StatusChangeEvent) => void) => void;
  onSettings: (cb: (data: SettingsEvent) => void) => void;
  onError: (cb: (data: ErrorEvent) => void) => void;
}

declare global {
  interface Window {
    dictaThesis: DictaThesisAPI;
  }
}
