#!/usr/bin/env python3
"""
richmor_config_app.py — desktop wrapper for the Richmor MDVR CONFIG/monitor app.

Part of the richmor_config.* toolset (separate from the main MDVR platform). It runs the existing
aiohttp server (richmor_config.py) in a background thread and shows it in a native OS webview window
(Edge WebView2 / Chromium on Windows, WKWebView on macOS, WebKitGTK on Linux). No browser install,
no terminal — a standalone app window that opens MAXIMIZED.

Run from source:   pip install pywebview ; python richmor_config_app.py
Build:             see richmor_config_app.spec  (pyinstaller richmor_config_app.spec)
"""
import os
import socket
import sys
import threading
import time

import richmor_config as server


def _wait_for_port(host: str, port: int, timeout: float = 20.0) -> bool:
    """Block until the server is accepting connections (or we give up)."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.15)
    return False


def main():
    # 1) start the web/control server on its own thread + event loop
    threading.Thread(target=server.run_server, daemon=True).start()

    # 2) wait for it to bind (server listens on WEB_HOST:WEB_PORT; the window talks to it via loopback)
    port = server.WEB_PORT
    if not _wait_for_port("127.0.0.1", port):
        print("Server did not start on 127.0.0.1:%d — aborting." % port, file=sys.stderr)
        sys.exit(1)

    # 3) open the native window, maximized
    try:
        import webview
    except ImportError:
        print("pywebview is not installed. Run:  pip install pywebview", file=sys.stderr)
        sys.exit(1)

    url = "http://127.0.0.1:%d/" % port
    try:
        webview.create_window("Richmor MDVR Config", url, maximized=True)
    except TypeError:
        # older pywebview without the 'maximized' kwarg — fall back to a large window
        webview.create_window("Richmor MDVR Config", url, width=1600, height=980)

    # Persist web storage (localStorage) so preferences survive restarts. private_mode=False + a
    # storage_path enables it; on WebKitGTK this also makes window.localStorage available at all.
    store = os.path.join(server.DATA_DIR, ".webview")
    try:
        os.makedirs(store, exist_ok=True)
    except Exception:
        store = None
    try:
        webview.start(private_mode=False, storage_path=store)
    except TypeError:
        webview.start()   # older pywebview without these kwargs


if __name__ == "__main__":
    main()
