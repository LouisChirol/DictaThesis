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

```bash
# Install dependencies
pip install -r requirements.txt

# Run
python main.py
```

On first run, right-click the tray icon → **Settings** to enter your Mistral API key.

### Linux note
pynput requires X11 for global hotkeys. On Wayland, set `GDK_BACKEND=x11` or use XWayland.

### macOS note
Grant **Accessibility** permission to Terminal (or your app) in  
System Preferences → Security & Privacy → Privacy → Accessibility.

---

## Configuration (Settings window)

| Setting | Description |
|---|---|
| **API Key** | Your Mistral API key |
| **Language** | FR / EN / Auto-detect |
| **Shortcut** | Default: F9 (single key, no modifier needed) |
| **Silence duration** | How long a pause ends a chunk (0.5–4.0 s) |
| **Vocabulary** | Custom terms (one per line) for typo correction |
| **Bibliography** | Paste BibTeX or reference list; used for `\cite{}` commands |

---

## Architecture

```
main.py          — entry point, threading model
pipeline.py      — two-pass state machine (Voxtral → Mistral)
audio.py         — sounddevice capture + VAD chunking
api_client.py    — async Mistral API calls (httpx)
injector.py      — clipboard + Ctrl/Cmd+V text injection
hud.py           — floating tkinter HUD (draft/final display)
tray.py          — pystray system tray icon and menu
prompt.py        — system prompt assembly + command mapping
settings_store.py — JSON config (~/.config/dictathesis/)
settings_ui.py   — settings window (tkinter)
```

## Tech stack

| Component | Library |
|---|---|
| Audio capture | `sounddevice` (PortAudio) |
| Voice activity detection | Energy VAD (Phase 1) → `webrtcvad` (Phase 3) |
| 1st pass STT | Mistral Voxtral (`voxtral-mini-latest`) |
| 2nd pass LLM | Mistral Medium (`mistral-medium-latest`) |
| Text injection | `pyperclip` + `pynput` |
| System tray | `pystray` |
| HUD window | `tkinter` |
| Global hotkey | `pynput` |

---

## Roadmap

- [x] Phase 1 — Core: F9 → record → Voxtral → inject
- [x] Phase 2 — Two-pass pipeline + voice commands
- [x] Phase 3 — VAD chunking + HUD (draft/final display)
- [ ] Phase 4 — Custom context (bibliography + vocabulary in prompt)
- [ ] Phase 5 — UX polish (sound feedback, draggable HUD, opacity)
- [ ] Phase 6 — Equation mode (LaTeX math dictation) + PyInstaller packaging
