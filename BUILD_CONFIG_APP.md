# Richmor MDVR Config — standalone desktop app

Part of the **richmor_config.\*** toolset (the local device config/monitor tool — separate from the main
MDVR platform). Wraps `richmor_config.py` in a native window (Edge WebView2 on Windows, WKWebView on
macOS, WebKitGTK on Linux) and opens **maximized**. No browser install, no terminal.

The server still binds `0.0.0.0:8090`, so other machines on the recorder's Wi‑Fi can still open it in a
browser at `http://<this-pc-ip>:8090` — the desktop window just talks to `127.0.0.1:8090` locally.

## Run from source (dev)

```
pip install -r requirements-config-app.txt
python richmor_config_app.py
```

## Build a single binary

```
pip install -r requirements-config-app.txt
pyinstaller richmor_config_app.spec
```

Output lands in `dist/` as **`RichmorConfig`** (`.exe` on Windows). It's a **one‑file** build: the HTML/JS,
the `assets/`, and the bundled **ffmpeg** (for clip downloads) are all inside it. Downloads/exports are
written next to the binary: `playbacks/` (muxed clips) and `logs/` (exported device logs).

> **PyInstaller is not a cross‑compiler.** Build on the OS you're shipping to:
> Windows build → Windows `.exe`; Linux build → Linux binary (ELF); macOS build → Mach‑O.

## Per‑OS notes

- **Windows** — safest target. WebView2 is Chromium, so live H.264 (MSE), WebAudio and WebSockets behave
  exactly like Chrome. WebView2 runtime ships with Windows 10/11; on older builds install the free
  "Microsoft Edge WebView2 Runtime" once. Produces a real `.exe`.
- **Linux** — install WebKitGTK first: `sudo apt install python3-gi gir1.2-webkit2-4.0 gir1.2-gtk-3.0`.
  The result is a **Linux binary, not a .exe**, and it uses the system WebKitGTK at runtime (so the
  target box needs those packages too — it isn't 100% self‑contained).
- **macOS** — uses WKWebView (WebKit). Config/streaming/playback work; **test the live H.264 view once**,
  as WebKit's MSE is stricter than Chromium's. Build on the Mac you target.

## Debugging a build

If the window is blank or nothing opens, set `console=False` → `console=True` in
`richmor_config_app.spec` and rebuild — you'll get a console with the server logs. Flip it back to ship.
