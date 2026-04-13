# DictaThesis: Migrate tkinter → PySide6

## Why
tkinter + pystray + pynput cause X11 threading crashes on WSL2/Linux. PySide6 handles GUI + tray + threading in one framework.

## Dependencies (pyproject.toml)

**Remove:** `pystray`, `Pillow`

**Add:** `PySide6>=6.6.0`, `qasync>=0.27.0`

## Files to Rewrite

### main.py
- Use `QApplication` + `qasync.QEventLoop` for asyncio integration
- Use Qt signals/slots for thread-safe updates from pipeline to HUD
- Detect WSL2 (`/proc/version` contains "microsoft") → disable pynput hotkeys there

### hud.py
- `QWidget` with flags: `Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool`
- Draggable via `mousePressEvent`/`mouseMoveEvent`
- Slots: `add_chunk(chunk_id, text)`, `finalize_chunk(chunk_id, text)`, `set_status(text)`, `clear()`
- Dark theme with QSS styling

### tray.py
- `QSystemTrayIcon` with `QMenu`
- Signals: `toggle_requested`, `settings_requested`, `quit_requested`
- Methods: `set_recording(bool)`, `set_processing()`, `set_idle()`
- Create icons with `QPixmap` + `QPainter` (colored circles)

### settings_ui.py
- `QDialog` with: API key input, language radio buttons, shortcut input, VAD slider, vocabulary/bibliography text areas, save button
- Load from / save to `SettingsStore`

## Keep Unchanged
`audio.py`, `pipeline.py`, `api_client.py`, `prompt.py`, `settings_store.py`, `injector.py`

## Implementation Order
1. Update pyproject.toml → `uv sync`
2. Rewrite hud.py
3. Rewrite tray.py  
4. Rewrite settings_ui.py
5. Rewrite main.py
6. Test
