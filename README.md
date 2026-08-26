# DictaThesis

Smart voice dictation for thesis writing — designed for one-handed use.

Works in **any** application: Google Docs, Word, Overleaf, TeXStudio, email, VSCode — no browser extension required. Text is injected at the cursor position via the system clipboard.

---

## How it works

1. Press **F9** (configurable) to start dictation
2. Speak your paper naturally — punctuation is inferred, not dictated word-by-word
3. **1st pass**: Voxtral Mini Transcribe 2 transcribes each chunk (vocabulary via `context_bias`)
4. **2nd pass**: Mistral Medium refines into thesis register, adds smart punctuation, and handles voice commands
5. The refined text is injected into whatever app is focused
6. Press **F9** again (or click Stop in the HUD) to end

Speak full sentences. You do **not** need to say "point" or "virgule" — the system adds French/English academic punctuation. Use distinctive phrases only for structure and editing (see below).

---

## Voice commands (FR / EN)

| Say | Result |
|---|---|
| "nouveau paragraphe" / "new paragraph" | paragraph break |
| "titre un" / "heading one" | `# ` prefix |
| "titre deux" / "heading two" | `## ` prefix |
| "référence numéro 3" / "reference number 3" | `\cite{ref3}` |
| "ouvrir parenthèse" / "open parenthesis" | `(` |
| "fermer parenthèse" / "close parenthesis" | `)` |
| "ouvrir guillemet" / "open quote" | `«` |
| "fermer guillemet" / "close quote" | `»` |
| "annuler la phrase précédente" / "delete previous sentence" | undo last sentence |
| "supprimer le mot précédent" / "delete previous word" | undo last word |
| "début équation" / "start equation" | `$` |
| "arrêter la dictée" / "stop dictation" | end session |

The HUD also has one-click chips for `( )` and `« »` when you prefer not to speak delimiters.

---

## Setup

### Prerequisites

- Python 3.11+
- Node.js 18+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- [Mistral API key](https://console.mistral.ai/)
- **Linux/WSL**: `sudo apt install libportaudio2 portaudio19-dev`
- **macOS**: `brew install portaudio`

### Install & Run

```bash
git clone https://github.com/your-username/DictaThesis.git
cd DictaThesis

# Install Python backend
cd python
uv sync
cd ..

# Install Electron frontend
cd app
npm install
npm start
```

### Mistral API Key

Click the gear icon in the HUD or right-click the tray icon → **Settings** to enter your API key.

Or edit directly: `~/.config/dictathesis/config.json` (Linux/macOS) or `%APPDATA%\DictaThesis\config.json` (Windows)

---

## Platform Notes

| Platform | Status |
|---|---|
| **Windows** | Full support |
| **macOS** | Grant Accessibility permission for hotkey |
| **Linux (X11)** | Full support |
| **WSL2** | Works — GPU acceleration auto-disabled |

---

## Configuration

| Setting | Description |
|---|---|
| **API Key** | Your Mistral API key |
| **Language** | FR / EN / Auto-detect |
| **Shortcut** | Default: F9 |
| **Silence duration** | Pause length before chunk ends (0.5–4.0 s) |
| **Vocabulary** | Custom terms — `context_bias` for STT + refinement in Medium |
| **Bibliography** | BibTeX for `\cite{}` commands |

---

## Architecture

```
DictaThesis/
  python/                    # Python backend (sidecar process)
    sidecar.py               — headless IPC bridge (stdin/stdout JSONL)
    pipeline.py              — two-pass state machine (Voxtral Mini → Medium)
    audio.py                 — sounddevice capture + VAD chunking
    api_client.py            — async Mistral API calls (httpx)
    injector.py              — clipboard + Ctrl/Cmd+V text injection
    prompt.py                — STT + refinement prompt assembly
    settings_store.py        — JSON config
    context_reader.py        — focused-field context at session start
    text_editor.py           — backspace-based editing commands

  app/                       # Electron frontend
    src/main/
      main.ts                — app entry, window management, lifecycle
      sidecar.ts             — Python process spawn + JSONL IPC
      tray.ts                — system tray with state icons
      shortcuts.ts           — global shortcut registration
      ipc-handlers.ts        — renderer ↔ sidecar bridge
      preload.ts             — contextBridge API
    src/renderer/
      index.html             — HUD overlay
      settings.html          — settings window
      styles/                — CSS (dark theme, clean modern)
      app/                   — TypeScript UI logic
```

## Tech Stack

| Component | Library |
|---|---|
| Desktop shell | Electron (TypeScript) |
| Audio capture | `sounddevice` (PortAudio) |
| Voice activity | Silero / WebRTC / energy VAD |
| 1st pass STT | Voxtral Mini Transcribe 2 |
| 2nd pass LLM | Mistral Medium |
| Text injection | `pyperclip` + `pynput` |
| Global hotkey | Electron `globalShortcut` |

---

## Roadmap

- [x] Phase 1 — Core: F9 → record → Voxtral → inject
- [x] Phase 2 — Two-pass pipeline + voice commands
- [x] Phase 3 — VAD chunking + HUD
- [x] Phase 4 — Electron migration (cross-platform GUI)
- [x] Phase 5 — Custom context (bibliography + vocabulary in STT + refine)
- [ ] Phase 6 — Equation mode (LaTeX math dictation)
- [ ] Phase 7 — Packaging (PyInstaller + Electron Forge)
- [ ] Phase 8 — Realtime Voxtral streaming HUD
