# DictaThesis

Smart voice dictation for thesis writing — designed for one-handed use.

Works in **any** application: Google Docs, Word, Overleaf, TeXStudio, email, VSCode — no browser extension required. Text is injected at the cursor position via the system clipboard.

---

## How it works

1. Press **F9** (configurable) to start dictation
2. Speak naturally — sentences are detected automatically
3. **1st pass**: Voxtral transcribes each sentence and shows it in the HUD immediately
4. **2nd pass**: Mistral Medium refines the text, fixes typos using your custom vocabulary, and interprets voice commands
5. The refined text is injected into whatever app is focused
6. Press **F9** again (or click Stop in the HUD) to end

---

## Voice commands (FR / EN)

| Say | Result |
|---|---|
| "point" / "period" | `.` |
| "virgule" / "comma" | `,` |
| "à la ligne" / "new line" | line break |
| "nouveau paragraphe" / "new paragraph" | paragraph break |
| "titre un" / "heading one" | `# ` prefix |
| "titre deux" / "heading two" | `## ` prefix |
| "référence numéro 3" / "reference number 3" | `\cite{ref3}` |
| "gras" / "bold" | `**...**` |
| "italique" / "italic" | `_..._` |
| "début équation" / "start equation" | switch to equation mode |
| "arrêter la dictée" / "stop dictation" | end session |

---

## Setup

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- [Mistral API key](https://console.mistral.ai/)
- **Linux/WSL**: `sudo apt install libportaudio2 portaudio19-dev libxcb-cursor0`
- **macOS**: `brew install portaudio`

### Install & Run

```bash
git clone https://github.com/your-username/DictaThesis.git
cd DictaThesis

# Install dependencies
uv sync

# Run
uv run python main.py
```

### Mistral API Key

On first run, right-click the tray icon → **Settings** to enter your API key.

Or edit directly: `~/.config/dictathesis/config.json` (Linux/macOS) or `%APPDATA%\DictaThesis\config.json` (Windows)

---

## Platform Notes

| Platform | Status |
|---|---|
| **Windows** | Full support |
| **macOS** | Grant Accessibility permission for hotkey |
| **Linux (X11)** | Full support |
| **WSL2** | Works — global hotkey disabled, use tray menu instead |

---

## Configuration

| Setting | Description |
|---|---|
| **API Key** | Your Mistral API key |
| **Language** | FR / EN / Auto-detect |
| **Shortcut** | Default: F9 |
| **Silence duration** | Pause length before chunk ends (0.5–4.0 s) |
| **Vocabulary** | Custom terms for typo correction |
| **Bibliography** | BibTeX for `\cite{}` commands |

---

## Architecture

```
main.py          — Qt app entry point, qasync event loop
hud.py           — floating PySide6 overlay (always-on-top)
tray.py          — QSystemTrayIcon + menu
settings_ui.py   — settings dialog (PySide6)
pipeline.py      — two-pass state machine (Voxtral → Mistral)
audio.py         — sounddevice capture + VAD chunking
api_client.py    — async Mistral API calls (httpx)
injector.py      — clipboard + Ctrl/Cmd+V text injection
prompt.py        — system prompt assembly
settings_store.py — JSON config
```

## Tech Stack

| Component | Library |
|---|---|
| GUI / System tray | `PySide6` (Qt) |
| Async integration | `qasync` |
| Audio capture | `sounddevice` (PortAudio) |
| Voice activity | `webrtcvad` |
| 1st pass STT | Mistral Voxtral |
| 2nd pass LLM | Mistral Medium |
| Text injection | `pyperclip` + `pynput` |
| Global hotkey | `pynput` (except WSL2) |

---

## Roadmap

- [x] Phase 1 — Core: F9 → record → Voxtral → inject
- [x] Phase 2 — Two-pass pipeline + voice commands
- [x] Phase 3 — VAD chunking + HUD
- [x] Phase 4 — PySide6 migration (cross-platform GUI)
- [ ] Phase 5 — Custom context (bibliography + vocabulary)
- [ ] Phase 6 — Equation mode (LaTeX math dictation)
- [ ] Phase 7 — PyInstaller packaging
