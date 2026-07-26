#!/usr/bin/env python3
"""
richmor_config.py — LOCAL, single-device config app for a Richmor MDVR over its own WiFi.

Unlike the cloud suite (richmor_main + listeners + MQTT), this talks DIRECTLY to one MDVR on its
native LAN service. You join the MDVR's WiFi AP (default SSID "R-MDVR" / pass "123456789"); the MDVR
answers on 10.10.10.1 — config/control on TCP 9003, live video on 9008 (JT1078 "01cd"). This server:

  * serves richmor_config.html (an exact-style copy of the dashboard, no left panel, single device)
  * maintains ONE TCP session to the MDVR's 9003 control channel (login 0x0C01 user "ceshi"/"123456",
    heartbeat 0x0002), and tracks whether the device is reachable
  * over a browser WebSocket (/ws) it broadcasts the connection state (up/down) — the page shows a
    "connect your WiFi to the MDVR" overlay while down, and hard-refreshes when it comes back
  * decodes the device's live GPS (native Info_gps) -> telemetry so Live Tracking works

Native framing is JT/T-808 style: 7e <msgid WORD> <attr WORD> <phone BCD6> <serial WORD> <body> <xor> 7e.
Run:  pip install aiohttp --break-system-packages ; python3 richmor_config.py
"""
import asyncio
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import time
from collections import deque

_IS_WIN = sys.platform.startswith("win")
_IS_MAC = sys.platform == "darwin"

from aiohttp import web, WSMsgType

import richmor_codec_native as N   # native LAN protocol (framing + 0x8C08 decode) lives in the codec

MDVR_HOST = os.environ.get("RICHMOR_MDVR_HOST", "10.10.10.1")
MDVR_PORT = int(os.environ.get("RICHMOR_MDVR_PORT", "9003"))      # native config/control channel
MEDIA_PORT = int(os.environ.get("RICHMOR_MEDIA_PORT", "9008"))    # real-time video (JT1078 "01cd")
WEB_HOST  = os.environ.get("RICHMOR_CFG_BIND", "0.0.0.0")
WEB_PORT  = int(os.environ.get("RICHMOR_CFG_PORT", "8090"))
AP_SSID   = os.environ.get("RICHMOR_AP_SSID", "R-MDVR")           # suggested default shown in the overlay
AP_PASS   = os.environ.get("RICHMOR_AP_PASS", "123456789")
# auto-WiFi: keep this machine joined to the MDVR's AP (Linux/NetworkManager, best-effort)
WIFI_MANAGE = os.environ.get("RICHMOR_WIFI_MANAGE", "1") not in ("0", "", "false", "no")
WIFI_SSID   = os.environ.get("RICHMOR_WIFI_SSID", "R-MDVR ")      # NOTE: real SSID has a trailing space
WIFI_PASS   = os.environ.get("RICHMOR_WIFI_PASS", AP_PASS)
WIFI_IFACE  = os.environ.get("RICHMOR_WIFI_IFACE", "")            # blank = let nmcli pick
WIFI_SUDO   = os.environ.get("RICHMOR_WIFI_SUDO", "0") not in ("0", "", "false", "no")  # force sudo -n for nmcli

def _res_dir():
    """Bundled read-only assets (HTML / codec JS / assets). Under a PyInstaller build this is the
    temporary extraction dir (sys._MEIPASS); running from source it's this file's folder."""
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def _data_dir():
    """Where the app keeps its writable state (playbacks/, logs/, .webview/, richmor_config.log).

    Packaged app: a per-user app-data folder so the .exe stays a clean single file with NOTHING
    created next to it (Windows %LOCALAPPDATA%\\RichmorConfig, macOS ~/Library/Application Support/
    RichmorConfig, Linux ~/.local/share/RichmorConfig). Running from source: this file's folder."""
    if getattr(sys, "frozen", False):
        if sys.platform == "win32":
            base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        elif sys.platform == "darwin":
            base = os.path.expanduser("~/Library/Application Support")
        else:
            base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        d = os.path.join(base, "RichmorConfig")
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            d = os.path.dirname(os.path.abspath(sys.executable))   # fallback if app-data isn't writable
        return d
    return os.path.dirname(os.path.abspath(__file__))


HERE = _res_dir()
DATA_DIR = _data_dir()
INDEX_HTML = os.path.join(HERE, "richmor_config.html")
ASSETS_DIR = os.path.join(HERE, "assets")
PLAYBACKS_DIR = os.path.join(DATA_DIR, "playbacks")   # muxed clip downloads land here, served at /playbacks/<name>
LOGS_DIR = os.path.join(DATA_DIR, "logs")             # exported device-log .txt files, served at /logexports/<name>
_CODEC_JS = {"richmor_codec_jt808.js", "richmor_codec_ttx.js", "richmor_codec_native.js"}

# ── ffmpeg (needed only for clip download → MP4). Cross-OS: use a system ffmpeg if present, else a
#    self-contained static build fetched via the imageio-ffmpeg wheel (no sudo; Win/mac/Linux). ──
FFMPEG = None


def _resolve_ffmpeg():
    """Fast, no-network lookup: env override -> PATH -> a bundled copy in ./bin -> None."""
    env = os.environ.get("RICHMOR_FFMPEG")
    if env and os.path.isfile(env):
        return env
    p = shutil.which("ffmpeg")
    if p:
        return p
    local = os.path.join(HERE, "bin", "ffmpeg.exe" if _IS_WIN else "ffmpeg")
    if os.path.isfile(local):
        return local
    return None


def _imageio_ffmpeg_exe():
    """Blocking: path to imageio-ffmpeg's bundled static ffmpeg (downloads on first call).
    Call via run_in_executor so it never blocks the event loop."""
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        return exe if exe and os.path.isfile(exe) else None
    except Exception:
        return None


async def _provision_ffmpeg():
    """Ensure FFMPEG points at a usable binary. Prefer system ffmpeg; otherwise install/fetch a
    static build once (imageio-ffmpeg). Runs in the background at startup — never blocks serving."""
    global FFMPEG
    FFMPEG = _resolve_ffmpeg()
    if FFMPEG:
        log.info("ffmpeg: using %s", FFMPEG)
        return
    exe = await _loop.run_in_executor(None, _imageio_ffmpeg_exe)   # already-installed bundle?
    if exe:
        FFMPEG = exe
        log.info("ffmpeg: using bundled %s", exe)
        return
    log.info("ffmpeg: not found — fetching a static build (one-time, needs internet)…")
    rc, out = await _run(sys.executable, "-m", "pip", "install", "--upgrade", "imageio-ffmpeg", timeout=300)
    if rc != 0 and not _IS_WIN:                       # PEP-668 managed environments
        rc, out = await _run(sys.executable, "-m", "pip", "install", "--upgrade",
                             "--break-system-packages", "imageio-ffmpeg", timeout=300)
    exe = await _loop.run_in_executor(None, _imageio_ffmpeg_exe)
    FFMPEG = exe
    if FFMPEG:
        log.info("ffmpeg: ready -> %s", FFMPEG)
    else:
        log.warning("ffmpeg: auto-provision failed. Install ffmpeg or set RICHMOR_FFMPEG=/path/to/ffmpeg. %s",
                    (out or "").strip()[-300:])

# the exact login the OEM app sends on 9003 (user "ceshi" / pass "123456"), captured verbatim -> proven to work
LOGIN_0C01 = bytes.fromhex(N.LOGIN_0C01_HEX)
HEARTBEAT_0002 = bytes.fromhex(N.HEARTBEAT_0002_HEX)   # generic heartbeat (phone/serial 0)

# Log to stdout AND to richmor_config.log beside the app (so the windowed build, which has no console,
# still leaves a copy-pasteable log). Fresh file each launch.
_LOG_FILE = os.path.join(DATA_DIR, "richmor_config.log")
_log_handlers = [logging.StreamHandler()]
try:
    _log_handlers.append(logging.FileHandler(_LOG_FILE, mode="w", encoding="utf-8"))
except Exception:
    pass
logging.basicConfig(level=logging.INFO, format="%(asctime)s.%(msecs)03d %(message)s",
                    datefmt="%H:%M:%S", handlers=_log_handlers)
log = logging.getLogger("config")
log.info("log file: %s", _LOG_FILE)

_clients = set()          # open browser WebSockets
_mdvr_up = False          # current reachability of the MDVR control channel
_loop = None
_tx_queue = None          # frames queued to send on the MDVR socket (single-writer)
_pending = {}             # subpackage reassembly state across TCP reads
_cfg_raw = {}             # last raw config block per param id (for read-modify-write)
_serial = 0               # rolling message serial
_read_waiters = {}        # param id -> Future, resolved when its 0x8C03 reply arrives (paced reads)
_cfg_busy = False         # a read/write sequence owns the device — block others, hide flaps from UI
_write_ack = None         # Future for the current write's 0x8001 universal ack

# ── media (real-time video, 9008) — one socket to the MDVR, demuxed per channel ──
_media_writer = None      # asyncio writer for the single 9008 media socket (None = not connected)
_media_task = None        # the media read loop task
_media_subs = {}          # channel -> set(_Subscriber)  (browser video WS viewers)
_media_key = {}           # channel -> last tagged keyframe (fast-start new viewers)
_media_buf = {}           # channel -> [bytearray, is_key]  (subpackage reassembly)
_media_req = {}           # channel -> (mode, media_type, bit_stream, begin, end) from last play/playback
_medialist_waiter = None  # Future for the current 0x9205 clip-list query (resolved by 0x1205)
_devlog_waiter = None     # Future for the current 0x0C06 device-log query (resolved by 0x8C06)
_link_quiet = False       # True around a query that makes this device cycle the 9003 link (device-log, and
_link_quiet_token = 0     # defensively the playback-list read) — suppress the resulting browser down/up blip
_dl = None                # the single active clip download: {"job", "cancel"} (mirrors the UI's one job)


def _next_serial():
    global _serial
    _serial = (_serial + 1) & 0xFFFF
    return _serial


def _send_frame(frame: bytes):
    """Queue a frame for the single writer (safe to call from the WS handler)."""
    if _tx_queue is not None:
        _tx_queue.put_nowait(frame)


# ── media relay: browser video WS <-> ONE 9008 socket to the MDVR (per-channel demux) ──
class _Subscriber:
    """One browser video WS. Bounded queue + drain task; drops oldest frame if the
    browser lags so a slow viewer never stalls the device read (mirrors streams.py)."""
    def __init__(self, ws):
        self.ws = ws
        self.q = deque(maxlen=300)
        self.event = asyncio.Event()

    def push(self, frame: bytes):
        self.q.append(frame)
        self.event.set()

    async def run(self):
        try:
            while True:
                if not self.q:
                    self.event.clear()
                    await self.event.wait()
                    continue
                await self.ws.send_bytes(self.q.popleft())
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        except Exception:
            pass


def _media_active():
    return any(_media_subs.get(c) for c in _media_subs)


def _media_send(frame: bytes):
    w = _media_writer
    if w is None:
        return
    try:
        w.write(frame)
    except Exception:
        pass


def _media_emit(ch, subs, frame, is_key):
    tagged = b"\x01" + frame                       # 0x01 = video tag (browser expects [tag][payload])
    if is_key:
        _media_key[ch] = tagged
    for s in subs:
        s.push(tagged)


def _media_dispatch(pkt):
    ch = pkt["channel"]
    subs = _media_subs.get(ch)
    if not subs:
        return
    dt, sp, pl = pkt["data_type"], pkt["subpkg"], pkt["payload"]
    if dt == N.MEDIA_DT_AUDIO:
        tagged = b"\x03" + pl                       # 0x03 = audio (G.711A, decoded client-side)
        for s in subs:
            s.push(tagged)
        return
    is_key = (dt == N.MEDIA_DT_I)
    if sp == N.MEDIA_SP_ATOM:
        _media_emit(ch, subs, pl, is_key)
    elif sp == N.MEDIA_SP_FIRST:
        _media_buf[ch] = [bytearray(pl), is_key]
    elif sp == N.MEDIA_SP_MIDDLE:
        b = _media_buf.get(ch)
        if b:
            b[0] += pl
    elif sp == N.MEDIA_SP_LAST:
        b = _media_buf.get(ch)
        if b:
            b[0] += pl
            _media_emit(ch, subs, bytes(b[0]), b[1])
            _media_buf.pop(ch, None)


async def _media_read(reader, writer):
    global _media_writer
    framer = N.MediaFramer()
    hb = _loop.time()
    idle_since = None
    try:
        while True:
            try:
                data = await asyncio.wait_for(reader.read(65536), timeout=5)
            except asyncio.TimeoutError:
                data = b""
            if data:
                for pkt in framer.feed(data):
                    _media_dispatch(pkt)
            elif reader.at_eof():
                break
            if _loop.time() - hb > 25:              # 30s keep-alive window (0xFC), like the OEM app
                try:
                    writer.write(N.build_media_keepalive())
                except Exception:
                    break
                hb = _loop.time()
            # keep the socket OPEN across a seek's brief stop->replay gap (~0.5s). Only drop it after a
            # sustained idle, so a seek reuses this socket instead of a flaky close+reconnect churn.
            if _media_active():
                idle_since = None
            else:
                if idle_since is None:
                    idle_since = _loop.time()
                elif _loop.time() - idle_since > 12:
                    break
    except Exception:
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass
        if _media_writer is writer:
            _media_writer = None
        log.info("media: 9008 closed")


async def _media_ensure():
    """Open the single 9008 media socket if not already up. Returns True on success."""
    global _media_writer, _media_task
    if _media_writer is not None:
        return True
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(MDVR_HOST, MEDIA_PORT), timeout=5)
    except Exception as e:
        log.warning("media: connect %s:%d failed: %s", MDVR_HOST, MEDIA_PORT, e)
        return False
    _media_writer = writer
    _media_task = asyncio.create_task(_media_read(reader, writer))
    log.info("media: connected %s:%d", MDVR_HOST, MEDIA_PORT)
    return True


async def _do_resources(ws, imei, channel, start, end):
    """Ask the device for its recorded-clip list (0x9205 on 9003), await the 0x1205 reply, and
    hand the browser {clips:[...]} — mirrors the platform's 'resources' contract."""
    global _medialist_waiter
    if not _mdvr_up:
        await ws.send_str(json.dumps({"type": "resources", "imei": imei, "data": {"error": "not_connected"}}))
        return
    fut = _loop.create_future()
    _medialist_waiter = fut
    _begin_link_quiet()                     # some firmware also cycles the link after the clip list — hide the blip
    _send_frame(N.build_media_query(channel, start, end, serial=_next_serial()))
    log.info("RESOURCES query ch%d %s..%s", channel, start, end)
    try:
        clips = await asyncio.wait_for(fut, timeout=30)
    except asyncio.TimeoutError:
        clips = None
    finally:
        if _medialist_waiter is fut:
            _medialist_waiter = None
    if clips is None:
        await ws.send_str(json.dumps({"type": "resources", "imei": imei, "data": {"error": "timeout"}}))
    else:
        log.info("RESOURCES %d clip(s)", len(clips))
        await ws.send_str(json.dumps({"type": "resources", "imei": imei, "data": {"clips": clips}}))


def _begin_link_quiet():
    """Mark that a query which cycles the 9003 link is in flight, so the drop+reconnect it triggers is
    not surfaced to the browser (no connect overlay / page reload). Latest caller governs the window."""
    global _link_quiet, _link_quiet_token
    _link_quiet = True
    _link_quiet_token += 1
    asyncio.create_task(_link_unquiet(_link_quiet_token))


async def _link_unquiet(token):
    """Hold the 'quiet' window open until the link has dropped and come back up + settled, then re-sync
    the browser. Capped at 20s so a genuine outage still surfaces; superseded if a newer query starts."""
    global _link_quiet
    t0 = _loop.time()
    while _loop.time() - t0 < 20:
        await asyncio.sleep(0.5)
        if _mdvr_up and (_loop.time() - t0) > 3:   # reconnected AND enough time to have covered the drop
            break
    if token != _link_quiet_token:                 # a newer query took over -> let that one clear it
        return
    _link_quiet = False
    log.info("LINK: settled (up=%s) — resuming normal browser notifications", _mdvr_up)
    await _broadcast(_mdvr_state_msg())      # tell the browser the true state (normally 'up' again — harmless)


async def _do_device_log(ws, imei, start, end, log_type, page, size):
    """Query the device's own operation/event log (0x0C06 on 9003), await the 0x8C06 reply, and
    hand the browser {count, entries:[{time,text}]} for one page. Paged by the caller (Load more)."""
    global _devlog_waiter
    if not _mdvr_up:
        await ws.send_str(json.dumps({"type": "device_log", "imei": imei, "page": page,
                                      "data": {"error": "not_connected"}}))
        return
    fut = _loop.create_future()
    _devlog_waiter = fut
    _begin_link_quiet()                     # device drops the link right after answering — hide the blip
    frame = N.build_device_log(start, end, log_type, page, size, serial=_next_serial())
    log.info("DEVLOG query %s..%s type=%d page=%d size=%d", start, end, log_type, page, size)
    log.info("DEVLOG TX 0x0C06 frame [%d B]: %s", len(frame), frame.hex())
    _send_frame(frame)
    try:
        res = await asyncio.wait_for(fut, timeout=30)
    except asyncio.TimeoutError:
        res = None
    finally:
        if _devlog_waiter is fut:
            _devlog_waiter = None
    if res is None:
        await ws.send_str(json.dumps({"type": "device_log", "imei": imei, "page": page,
                                      "data": {"error": "timeout"}}))
    else:
        log.info("DEVLOG %d entr(y/ies) (count=%d)", len(res.get("entries", [])), res.get("count", 0))
        await ws.send_str(json.dumps({"type": "device_log", "imei": imei, "page": page, "data": res}))


async def _do_device_log_export(ws, imei, entries, start, end):
    """Write the log lines the browser currently has loaded to a .txt next to config.py (like clip
    downloads — no browser save-dialog, no permission surprises) and hand back the filename + URL."""
    try:
        os.makedirs(LOGS_DIR, exist_ok=True)
        safe = "".join(c for c in (imei or "device") if c.isalnum()) or "device"
        s = "".join(c for c in (start or "") if c.isdigit())[:14]
        e = "".join(c for c in (end or "") if c.isdigit())[:14]
        filename = "log_%s_%s-%s.txt" % (safe, s or "start", e or "end")
        path = os.path.join(LOGS_DIR, filename)
        lines = ["Richmor MDVR device log", "Device: %s" % (imei or "device"),
                 "Range : %s .. %s" % (start or "?", end or "?"),
                 "Entries: %d" % len(entries), "-" * 60]
        for it in entries:
            lines.append("%s  %s" % (it.get("time", ""), it.get("text", "")))
        await _loop.run_in_executor(None, lambda: open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n"))
        log.info("DEVLOG export -> %s (%d entr%s)", filename, len(entries), "y" if len(entries) == 1 else "ies")
        await ws.send_str(json.dumps({"type": "device_log_export",
                                      "data": {"filename": filename, "url": "/logexports/" + filename,
                                               "count": len(entries), "local": True}}))
    except Exception as ex:
        log.warning("DEVLOG export failed: %s", ex)
        await ws.send_str(json.dumps({"type": "device_log_export", "data": {"error": str(ex)}}))


async def _do_download(imei, channel, start, end, expected, dur):
    """Reproduce the OEM 'download': open a VOD (0xF8) for the range on a dedicated 9008 socket,
    capture + reassemble the H.264 (and G.711A) frames as they play, mux to MP4 with ffmpeg, serve
    it. Progress streams to the browser (pb_dl_started / progress / complete / failed / cancelled)."""
    global _dl
    job = "%d" % int(time.time() * 1000)
    _dl = {"job": job, "cancel": False}
    safe = "".join(c for c in (imei or "device") if c.isalnum()) or "device"
    stamp = "".join(c for c in (start or "") if c.isdigit())[:14]
    filename = "%s_CH%d_%s.mp4" % (safe, channel, stamp or job)
    os.makedirs(PLAYBACKS_DIR, exist_ok=True)
    vpath = os.path.join(PLAYBACKS_DIR, "." + job + ".h264")
    apath = os.path.join(PLAYBACKS_DIR, "." + job + ".alaw")
    outpath = os.path.join(PLAYBACKS_DIR, filename)

    def _cleanup(*paths):
        for p in paths:
            try:
                os.remove(p)
            except OSError:
                pass

    async def _fail(msg):
        global _dl
        _dl = None
        _cleanup(vpath, apath)
        await _broadcast({"type": "pb_dl_failed", "data": {"job": job, "msg": msg}})

    await _broadcast({"type": "pb_dl_started", "data": {"imei": imei, "job": job, "filename": filename}})
    # the device speaks one 9008 socket at a time (like the OEM MediaConnector) — release the shared
    # viewer socket before opening our dedicated capture socket.
    global _media_writer
    if _media_writer is not None:
        try:
            _media_writer.close()
        except Exception:
            pass
        _media_writer = None
        await asyncio.sleep(0.6)
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(MDVR_HOST, MEDIA_PORT), timeout=6)
    except Exception as e:
        await _fail("cannot reach recorder (%s)" % e)
        return

    framer = N.MediaFramer()
    vbuf, abuf, reasm, vframes = bytearray(), bytearray(), None, 0
    writer.write(N.build_media_playback(channel, start, end, N.MEDIA_BOTH, N.MEDIA_MAIN))
    log.info("DOWNLOAD ch%d %s..%s job=%s", channel, start, end, job)
    t0 = _loop.time(); last_data = t0; last_prog = 0.0; total_rx = 0; why = "?"
    cap = (dur or 60) + 30                              # hard cap: clip length + slack (VOD plays ~1x)
    try:
        while True:
            if _dl is None or _dl.get("cancel"):
                await _broadcast({"type": "pb_dl_cancelled", "data": {"job": job}})
                _cleanup(vpath, apath)
                _dl = None
                log.info("DOWNLOAD job=%s cancelled after %dB rx", job, total_rx)
                return
            try:
                data = await asyncio.wait_for(reader.read(65536), timeout=3)
            except asyncio.TimeoutError:
                data = b""
            now = _loop.time()
            if data:
                total_rx += len(data); last_data = now
                for pkt in framer.feed(data):
                    if pkt["channel"] != channel:
                        continue
                    if pkt["data_type"] == N.MEDIA_DT_AUDIO:
                        abuf += pkt["payload"]
                        continue
                    sp = pkt["subpkg"]
                    if sp == N.MEDIA_SP_ATOM:
                        vbuf += pkt["payload"]; vframes += 1
                    elif sp == N.MEDIA_SP_FIRST:
                        reasm = bytearray(pkt["payload"])
                    elif sp == N.MEDIA_SP_MIDDLE:
                        if reasm is not None:
                            reasm += pkt["payload"]
                    elif sp == N.MEDIA_SP_LAST:
                        if reasm is not None:
                            reasm += pkt["payload"]; vbuf += reasm; vframes += 1; reasm = None
            else:
                if reader.at_eof():
                    why = "eof"; break
                if vbuf and now - last_data > 3:       # stream idled after data -> segment finished
                    why = "idle"; break
                if not vbuf and now - t0 > 12:          # device never sent anything -> give up early
                    why = "no-data"; break
            if now - t0 > cap:
                why = "cap"; break
            if now - last_prog > 0.8:                  # progress ~1/s (+ server log so we can watch)
                last_prog = now
                pct = min(97, int((now - t0) / max(dur, 1) * 100)) if dur else min(97, len(vbuf) // 20000)
                log.info("DOWNLOAD job=%s ... rx=%dB video_frames=%d video=%dB audio=%dB pct=%d",
                         job, total_rx, vframes, len(vbuf), len(abuf), pct)
                await _broadcast({"type": "pb_dl_progress",
                                  "data": {"job": job, "pct": pct, "video_bytes": len(vbuf)}})
    finally:
        try:
            writer.write(N.build_media_playback_ctrl(channel, N.VOD_STOP))
            writer.close()
        except Exception:
            pass

    log.info("DOWNLOAD job=%s capture loop ended (%s): rx=%dB video_frames=%d video=%dB audio=%dB",
             job, why, total_rx, vframes, len(vbuf), len(abuf))
    if _dl is None or _dl.get("cancel"):
        _cleanup(vpath, apath)
        _dl = None
        return
    if not vbuf:
        await _fail("no video captured for this range (device sent %dB)" % total_rx)
        return

    fps = max(1, round(vframes / max(dur, 1))) if dur else 15   # server reconstructs true fps from dur
    log.info("DOWNLOAD job=%s capture done: %d video frames, %dB video, %dB audio, dur=%ss -> fps=%d",
             job, vframes, len(vbuf), len(abuf), dur, fps)
    if not FFMPEG:
        await _fail("ffmpeg is still being installed (first run) — please try the download again shortly")
        return
    await _broadcast({"type": "pb_dl_progress", "data": {"job": job, "status": "muxing"}})
    with open(vpath, "wb") as f:
        f.write(vbuf)
    cmd = [FFMPEG, "-y", "-f", "h264", "-r", str(fps), "-i", vpath]
    if abuf:
        with open(apath, "wb") as f:
            f.write(abuf)
        cmd += ["-f", "alaw", "-ar", "8000", "-ac", "1", "-i", apath, "-c:v", "copy", "-c:a", "aac"]
    else:
        cmd += ["-c:v", "copy"]
    cmd += ["-movflags", "+faststart", outpath]
    log.info("DOWNLOAD job=%s ffmpeg: %s", job, " ".join(cmd))
    rc, out = await _run(*cmd, timeout=240)
    _cleanup(vpath, apath)
    if _dl is None or _dl.get("cancel"):
        _cleanup(outpath)
        _dl = None
        return
    size = os.path.getsize(outpath) if os.path.isfile(outpath) else 0
    if rc != 0 or size == 0:
        log.warning("DOWNLOAD job=%s FFMPEG FAILED rc=%s size=%d — output tail:\n%s",
                    job, rc, size, (out or "").strip()[-1800:])
        await _fail("ffmpeg mux failed — see server log")
        return
    _dl = None
    log.info("DOWNLOAD done job=%s -> %s (%d frames @%dfps, %dB mp4)", job, filename, vframes, fps, size)
    await _broadcast({"type": "pb_dl_complete",       # LOCAL: file already saved beside config.py; no browser fetch
                      "data": {"job": job, "filename": filename, "url": "/playbacks/" + filename, "local": True}})


async def playbacks_file(request):
    """Serve a finished MP4 from PLAYBACKS_DIR (HEAD for the readiness poll, GET to save)."""
    name = os.path.basename(request.match_info["name"])
    path = os.path.join(PLAYBACKS_DIR, name)
    if name.startswith(".") or not os.path.isfile(path):
        return web.Response(status=404)
    return web.FileResponse(path)


async def clientlog(request):
    """Receive a browser-side JS error (beacon) so it lands in richmor_config.log — the packaged
    windowed app has no devtools/console, so this is how we see front-end errors."""
    msg = request.query.get("m", "")[:2000]
    if msg:
        log.warning("CLIENT JS: %s", msg)
    return web.Response(text="ok")


async def logexport_file(request):
    """Serve an exported device-log .txt from LOGS_DIR."""
    name = os.path.basename(request.match_info["name"])
    path = os.path.join(LOGS_DIR, name)
    if name.startswith(".") or not os.path.isfile(path):
        return web.Response(status=404)
    return web.FileResponse(path)


async def video_handler(request):
    """Browser video WS: ws://host:PORT/video/ch<n>. First viewer of a channel starts the
    device stream (0xFD REQLIVE / 0xF8 VOD); when the last leaves we stop it."""
    ch = int(request.match_info["ch"])
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    subs = _media_subs.setdefault(ch, set())
    first = not subs
    sub = _Subscriber(ws)
    subs.add(sub)
    if not await _media_ensure():
        subs.discard(sub)
        await ws.close()
        return ws
    if first:
        req = _media_req.get(ch, ("live", N.MEDIA_BOTH, N.MEDIA_MAIN, None, None))
        mode, mt, bs = req[0], req[1], req[2]
        if mode == "playback":
            _media_send(N.build_media_playback(ch, req[3], req[4], mt, bs))
            log.info("media: playback ch%d %s..%s (type=%d stream=%d)", ch, req[3], req[4], mt, bs)
        else:
            _media_send(N.build_media_reqlive(ch, mt, bs))
            log.info("media: live ch%d (type=%d stream=%d)", ch, mt, bs)
    if _media_key.get(ch):
        sub.push(_media_key[ch])                    # fast-start on the cached keyframe
    drain = asyncio.create_task(sub.run())
    log.info("media: viewer + ch%d (now %d)", ch, len(subs))
    try:
        async for msg in ws:
            if msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                break
    finally:
        subs.discard(sub)
        drain.cancel()
        log.info("media: viewer - ch%d (now %d)", ch, len(subs))
        if not subs:
            req = _media_req.get(ch)
            if req and req[0] == "playback":
                _media_send(N.build_media_playback_ctrl(ch, N.VOD_STOP))   # stop VOD (0xF7)
            else:
                _media_send(N.build_media_stop(ch))                        # stop live (0xFB)
            _media_subs.pop(ch, None)
            _media_key.pop(ch, None)
            _media_buf.pop(ch, None)
            log.info("media: stop ch%d", ch)
    return ws


# ── browser broadcast ────────────────────────────────────────────────────────
async def _broadcast(obj):
    dead = []
    for ws in list(_clients):
        try:
            await ws.send_str(json.dumps(obj))
        except Exception:
            dead.append(ws)
    for ws in dead:
        _clients.discard(ws)


def _mdvr_state_msg():
    return {"type": "mdvr", "state": "up" if _mdvr_up else "down",
            "host": MDVR_HOST, "port": MDVR_PORT, "ssid": AP_SSID}


def _set_state(up):
    global _mdvr_up
    if up != _mdvr_up:
        _mdvr_up = up
        tag = "  (expected link cycle — browser not notified)" if _link_quiet else ""
        log.info("MDVR %s%s", "CONNECTED" if up else "disconnected", tag)
        # during a read/write (or a device-log query) the operation owns the link — don't tell the
        # browser about the transient drops it causes (no connect overlay / reload). State is
        # re-synced when it ends.
        if _loop and not _cfg_busy and not _link_quiet:
            asyncio.run_coroutine_threadsafe(_broadcast(_mdvr_state_msg()), _loop)


# ── native frame handling ────────────────────────────────────────────────────
# framing + 0x8C08 decode live in richmor_codec_native (project rule: protocol logic stays in a codec)

async def _route(msgid: int, serial: int, body: bytes):
    """Route one reassembled native message. Status polls (0x8C08) are silent; config read/write
    frames are logged."""
    if msgid == N.MSG_STATUS:                     # 0x8C08 status push (~1/s) -> Live Data (not logged)
        st = N.parse_status_8c08(body)
        if not st:
            return
        imei = st.get("device_no") or "device"
        st["ts"] = int(time.time() * 1000)
        await _broadcast({"type": "native", "imei": imei, "data": st})
        return
    if msgid == N.MSG_MEDIA_LIST_ACK:             # 0x1205 recorded-clip list reply
        r = N.parse_media_list(body)
        if _medialist_waiter is not None and not _medialist_waiter.done():
            _medialist_waiter.set_result(r.get("clips", []))
        return
    if msgid == N.MSG_DEVICE_LOG_ACK:             # 0x8C06 device operation/event log reply
        log.info("DEVLOG RX 0x8C06 body [%d B]: %s", len(body), body.hex())
        r = N.parse_device_log(body)
        log.info("DEVLOG RX parsed: count=%d entries=%d", r.get("count", 0), len(r.get("entries", [])))
        for e in r.get("entries", [])[:60]:
            log.info("   %s  (body len=%d) | %s", e.get("time", ""), e.get("len", 0), e.get("text", ""))
        if _devlog_waiter is not None and not _devlog_waiter.done():
            _devlog_waiter.set_result(r)
        return
    if msgid == N.MSG_QUERY_ACK:                  # 0x8C03 config-read reply
        r = N.parse_query_response(body)
        param = r.get("param_id")
        if not param:
            return
        block = bytes.fromhex(r.get("block_hex", ""))
        _cfg_raw[param] = block
        fields = N.read_fields(param, block)
        log.info("READ  0x%04X %-18s %dB  (%d fields)", param, r.get("section") or "?", len(block), len(fields))
        log.info("   body [%d]: %s", len(body), body.hex())      # full 0x8C03 payload as received (compare vs write)
        log.info("   block[%d]: %s", len(block), block.hex())    # section block — matches the write 'was' line
        await _broadcast({"type": "config", "param": param, "section": r.get("section"), "fields": fields})
        w = _read_waiters.get(param)                 # unblock the paced reader
        if w and not w.done():
            w.set_result(True)
        return
    if msgid == N.MSG_LOGIN_ACK:                  # 0x8C01
        a = N.parse_login_ack(body)
        log.info("LOGIN ack ok=%s media_port=%s", a.get("ok"), a.get("media_port"))
        return
    if msgid in (N.MSG_UNIVERSAL_ACK, 0x8001):    # write / action result
        a = N.parse_universal_ack(body)
        log.info("WRITE ack id=0x%04X result=%d %s", a.get("msg_id", 0), a.get("result", -1),
                 "OK" if a.get("ok") else "FAIL")
        if _write_ack is not None and not _write_ack.done():
            _write_ack.set_result(a)
        else:
            await _broadcast({"type": "config_ack", "msg_id": a.get("msg_id"), "result": a.get("result"), "ok": a.get("ok")})
        return


# ── WiFi service: cross-platform scan / connect / disconnect, driven by the browser ─
# The browser drives WiFi (list -> pick -> Connect). Python runs the OS's native WiFi tool and
# reports state. Designed to work on a plain user account with NO manual setup:
#   * Windows  -> netsh wlan  (adding a per-user profile + connecting needs NO admin rights)
#   * Linux    -> nmcli       (works in a desktop session; if the session policy refuses, we fall
#                              back to a standard graphical auth prompt via pkexec — no file edits)
_wifi_cur = ""            # last-known joined SSID ("" = not on any WiFi)


async def _run(*args, timeout=25):
    """Run a command, return (rc, stdout). Never raises."""
    try:
        p = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(p.communicate(), timeout=timeout)
        return p.returncode, (out or b"").decode("utf-8", "ignore")
    except Exception as e:
        return -1, str(e)


# ---- OS dispatch ----
async def _current_ssid():
    return await (_win_current_ssid() if _IS_WIN else _mac_current_ssid() if _IS_MAC else _nix_current_ssid())


async def _wifi_scan():
    """[{ssid, signal, secure}] strongest-first. SSID keeps its exact spelling (incl. a trailing space)."""
    return await (_win_scan() if _IS_WIN else _mac_scan() if _IS_MAC else _nix_scan())


async def _wifi_do_connect(ssid, password):
    ok, msg = await (_win_connect(ssid, password) if _IS_WIN
                     else _mac_connect(ssid, password) if _IS_MAC
                     else _nix_connect(ssid, password))
    if ok:
        log.info("WiFi: joined %r", ssid)
    else:
        log.warning("WiFi: connect %r failed: %s", ssid, msg)
    return ok, msg


async def _wifi_disconnect():
    return await (_win_disconnect() if _IS_WIN else _mac_disconnect() if _IS_MAC else _nix_disconnect())


# ===== Linux / NetworkManager (nmcli) =========================================
async def _nix_current_ssid():
    rc, out = await _run("iwgetid", "-r", timeout=6)         # simplest: prints the joined SSID
    if rc == 0:
        return out.strip("\n")                               # keep a trailing space in the SSID; drop only newline
    rc, out = await _run("nmcli", "-t", "-f", "active,ssid", "dev", "wifi", timeout=8)
    for line in out.splitlines():
        if line.startswith("yes:"):
            return line[4:]
    return ""


async def _run_priv(*args, timeout=35):
    """Run a state-changing nmcli command. Try in-session first (works for a logged-in desktop user);
    if the policy refuses, raise the standard graphical auth dialog via pkexec — never edits sudoers."""
    if WIFI_SUDO:
        return await _run("sudo", "-n", *args, timeout=timeout)
    rc, out = await _run(*args, timeout=timeout)
    low = out.lower()
    if rc != 0 and ("insufficient privileges" in low or "not authorized" in low or "not privileged" in low):
        if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
            log.info("WiFi: not authorized in-session — asking via pkexec (graphical prompt)")
            rc, out = await _run("pkexec", *args, timeout=max(timeout, 120))   # user types password in a GUI dialog
        else:
            rc, out = await _run("sudo", "-n", *args, timeout=timeout)          # headless: relies on a NOPASSWD rule
    return rc, out


async def _wifi_iface():
    if WIFI_IFACE:
        return WIFI_IFACE
    rc, out = await _run("nmcli", "-t", "-f", "DEVICE,TYPE", "device", "status", timeout=8)
    for line in out.splitlines():
        if line.endswith(":wifi"):
            return line.split(":", 1)[0]
    return ""


async def _nix_scan():
    rc, out = await _run("nmcli", "-t", "-f", "SIGNAL,SECURITY,SSID",
                         "device", "wifi", "list", "--rescan", "auto", timeout=25)
    seen = {}
    for line in out.splitlines():
        parts = line.split(":", 2)                           # SIGNAL:SECURITY:SSID  (ssid is last, keeps colons/spaces)
        if len(parts) < 3:
            continue
        sig, sec, ssid = parts
        if not ssid:                                         # hidden network -> skip
            continue
        try:
            sig = int(sig)
        except ValueError:
            sig = 0
        if ssid not in seen or sig > seen[ssid]["signal"]:
            seen[ssid] = {"ssid": ssid, "signal": sig, "secure": bool(sec and sec != "--")}
    return sorted(seen.values(), key=lambda x: -x["signal"])


async def _nix_connect(ssid, password):
    args = ["nmcli", "device", "wifi", "connect", ssid]
    if password:
        args += ["password", password]
    if WIFI_IFACE:
        args += ["ifname", WIFI_IFACE]
    rc, out = await _run_priv(*args, timeout=35)
    return rc == 0, (out.strip().splitlines() or [""])[-1]


async def _nix_disconnect():
    iface = await _wifi_iface()
    if not iface:
        return False, "no WiFi interface"
    rc, out = await _run_priv("nmcli", "device", "disconnect", iface, timeout=20)
    return rc == 0, (out.strip().splitlines() or [""])[-1]


# ===== macOS (networksetup + airport) — connect/current are solid; scan is best-effort ==========
_MAC_AIRPORT = "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport"


async def _mac_iface():
    if WIFI_IFACE:
        return WIFI_IFACE
    rc, out = await _run("networksetup", "-listallhardwareports", timeout=8)
    lines = out.splitlines()
    for i, ln in enumerate(lines):
        if "Wi-Fi" in ln or "AirPort" in ln:                 # "Hardware Port: Wi-Fi" then "Device: en0"
            for j in range(i + 1, min(i + 3, len(lines))):
                if lines[j].strip().startswith("Device:"):
                    return lines[j].split(":", 1)[1].strip()
    return "en0"


async def _mac_current_ssid():
    iface = await _mac_iface()
    rc, out = await _run("networksetup", "-getairportnetwork", iface, timeout=8)
    if "not associated" in out.lower() or ":" not in out:
        return ""
    v = out.split(":", 1)[1].rstrip("\r\n")                  # "Current Wi-Fi Network: R-MDVR "
    return v[1:] if v.startswith(" ") else v


async def _mac_scan():
    rc, out = await _run(_MAC_AIRPORT, "-s", timeout=20)      # deprecated on newer macOS -> may be empty
    nets = {}
    for ln in out.splitlines()[1:]:                          # skip header row
        m = re.search(r"([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})\s+(-?\d+)", ln)   # BSSID + RSSI
        if not m:
            continue
        ssid = ln[:m.start()].rstrip()                       # SSID is the field before the BSSID
        sig = max(0, min(100, 2 * (int(m.group(2)) + 100)))  # RSSI dBm -> rough 0..100%
        sec = any(k in ln for k in ("WPA", "WEP", "RSN"))
        if ssid and (ssid not in nets or sig > nets[ssid]["signal"]):
            nets[ssid] = {"ssid": ssid, "signal": sig, "secure": sec}
    return sorted(nets.values(), key=lambda x: -x["signal"])


async def _mac_connect(ssid, password):
    iface = await _mac_iface()
    await _run("networksetup", "-setairportpower", iface, "on", timeout=10)
    args = ["networksetup", "-setairportnetwork", iface, ssid]
    if password:
        args.append(password)
    rc, out = await _run(*args, timeout=30)
    low = out.lower()
    ok = rc == 0 and "could not" not in low and "failed" not in low and "error" not in low
    return ok, (out.strip().splitlines() or ["connected"])[-1]


async def _mac_disconnect():
    # no clean userland disassociate on macOS -> turn the radio off; the picker's Connect turns it back on
    iface = await _mac_iface()
    rc, out = await _run("networksetup", "-setairportpower", iface, "off", timeout=15)
    return rc == 0, "wifi turned off — pick a network to reconnect"


# ===== Windows (netsh wlan) — no admin rights required for a per-user profile ===
def _netsh_val(line):
    """Value after the first ':' in a netsh line, keeping trailing spaces (only the CR + one pad space removed)."""
    i = line.find(":")
    if i < 0:
        return ""
    v = line[i + 1:].rstrip("\r\n")
    return v[1:] if v.startswith(" ") else v


async def _win_current_ssid():
    rc, out = await _run("netsh", "wlan", "show", "interfaces", timeout=10)
    for line in out.splitlines():
        key = line.split(":", 1)[0].strip()
        if key == "SSID":                                    # exact key -> not "BSSID"
            return _netsh_val(line)
    return ""


async def _win_scan():
    rc, out = await _run("netsh", "wlan", "show", "networks", "mode=bssid", timeout=20)
    seen, cur = {}, None
    for line in out.splitlines():
        key = line.split(":", 1)[0].strip()
        if re.match(r"SSID\s+\d+$", key):                    # "SSID 1", "SSID 2", ...  (block start)
            cur = _netsh_val(line)                           # keep exact spelling incl. trailing space
            if cur and cur not in seen:
                seen[cur] = {"ssid": cur, "signal": 0, "secure": True}
        elif cur:
            m = re.search(r"(\d{1,3})\s*%", line)            # "Signal : 92%"  (label may be localized -> match the %)
            if m:
                s = int(m.group(1))
                if s > seen[cur]["signal"]:
                    seen[cur]["signal"] = s
    return sorted((v for v in seen.values() if v["ssid"]), key=lambda x: -x["signal"])


def _win_profile_xml(ssid, password):
    from xml.sax.saxutils import escape
    name = escape(ssid)
    hexssid = ssid.encode("utf-8").hex().upper()             # hex SSID -> exact match even with a trailing space
    if password:
        sec = ("<security><authEncryption><authentication>WPA2PSK</authentication>"
               "<encryption>AES</encryption><useOneX>false</useOneX></authEncryption>"
               "<sharedKey><keyType>passPhrase</keyType><protected>false</protected>"
               f"<keyMaterial>{escape(password)}</keyMaterial></sharedKey></security>")
    else:
        sec = ("<security><authEncryption><authentication>open</authentication>"
               "<encryption>none</encryption><useOneX>false</useOneX></authEncryption></security>")
    return ('<?xml version="1.0"?>\n'
            '<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">'
            f'<name>{name}</name>'
            f'<SSIDConfig><SSID><hex>{hexssid}</hex><name>{name}</name></SSID></SSIDConfig>'
            '<connectionType>ESS</connectionType><connectionMode>manual</connectionMode>'
            f'<MSM>{sec}</MSM></WLANProfile>')


async def _win_connect(ssid, password):
    path = None
    try:
        fd, path = tempfile.mkstemp(suffix=".xml", prefix="rmdvr_")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(_win_profile_xml(ssid, password))
        rc, out = await _run("netsh", "wlan", "add", "profile",
                             "filename=" + path, "user=current", timeout=15)
        if rc != 0:
            return False, (out.strip().splitlines() or ["could not add WiFi profile"])[-1]
    finally:
        if path:
            try:
                os.remove(path)
            except OSError:
                pass
    rc, out = await _run("netsh", "wlan", "connect", "name=" + ssid, "ssid=" + ssid, timeout=20)
    if rc != 0:
        return False, (out.strip().splitlines() or ["connect command failed"])[-1]
    for _ in range(12):                                      # netsh returns instantly; wait for association
        await asyncio.sleep(1)
        if (await _win_current_ssid()).strip() == ssid.strip():
            return True, "connected"
    return False, "could not associate — check the password"


async def _win_disconnect():
    rc, out = await _run("netsh", "wlan", "disconnect", timeout=15)
    return rc == 0, (out.strip().splitlines() or [""])[-1]


def _wifi_state_msg():
    on_target = _wifi_cur.strip() == WIFI_SSID.strip()
    return {"type": "wifi", "connected": bool(_wifi_cur), "ssid": _wifi_cur,
            "on_target": on_target, "target": WIFI_SSID}


async def _wifi_monitor():
    """Poll the joined SSID and push state to the browser on change. Read-only; never auto-joins."""
    global _wifi_cur
    if not WIFI_MANAGE:
        return
    while True:
        cur = await _current_ssid()
        if cur != _wifi_cur:
            _wifi_cur = cur
            log.info("WiFi: now on %r", cur or "(none)")
            await _broadcast(_wifi_state_msg())
        await asyncio.sleep(4)


# ── the single MDVR control session ──────────────────────────────────────────
async def _tx_pump(writer):
    """Sole writer to the MDVR socket: sends queued config frames + a 15s keepalive.
    A small gap after each send keeps the device from being flooded."""
    last_hb = _loop.time()
    while True:
        try:
            frame = await asyncio.wait_for(_tx_queue.get(), timeout=5)
            writer.write(frame); await writer.drain()
            await asyncio.sleep(0.08)
        except asyncio.TimeoutError:
            pass
        if _loop.time() - last_hb > 15:
            writer.write(HEARTBEAT_0002); await writer.drain(); last_hb = _loop.time()


async def _await_up(timeout=8):
    """Wait (briefly) for the MDVR link to come back after one of its periodic drops."""
    t0 = _loop.time()
    while not _mdvr_up and _loop.time() - t0 < timeout:
        await asyncio.sleep(0.3)
    return _mdvr_up


async def _read_one(p):
    """Send one query, wait for its reply. Returns 'ok' | 'timeout' (device up, silent) |
    'poison' (the link dropped — this param likely closed it)."""
    fut = _loop.create_future()
    _read_waiters[p] = fut
    _send_frame(N.build_query([p], _next_serial()))
    try:
        await asyncio.wait_for(fut, timeout=2.5)
        return "ok"
    except asyncio.TimeoutError:
        return "poison" if not _mdvr_up else "timeout"
    finally:
        _read_waiters.pop(p, None)


async def _read_section(p):
    """Read one section robustly: on a drop, wait for reconnect and retry ONCE. If it drops the link
    AGAIN it is confirmed poison (like 0xF017) -> give up on it and let the caller continue."""
    if not _mdvr_up and not await _await_up():
        return False
    r = await _read_one(p)
    if r == "ok":
        return True
    if not _mdvr_up:
        await _await_up()                          # let it come back before retry
    r2 = await _read_one(p)
    if r2 == "ok":
        return True
    if r2 == "poison":
        log.warning("skip 0x%04X — closes the link on query (firmware quirk)", p)
        await _await_up()
    return False


async def _do_read(params):
    """Exclusive, paced read of every section, one at a time. Any param that closes the link is
    skipped after one retry; the reconnect is handled silently in the background."""
    global _cfg_busy
    _cfg_busy = True
    done, skipped = 0, []
    try:
        for p in params:
            ok = await _read_section(p)
            if ok:
                done += 1
            else:
                skipped.append(p)
            await _broadcast({"type": "read_progress", "done": done, "total": len(params)})
    finally:
        _cfg_busy = False
        if _loop:
            await _broadcast(_mdvr_state_msg())    # re-sync the browser after we release the link
    miss = (" (skipped: %s)" % [hex(x) for x in skipped]) if skipped else ""
    log.info("READ  done: %d/%d section(s)%s", done, len(params), miss)
    await _broadcast({"type": "read_done", "done": done, "total": len(params)})


async def _write_one(param, block):
    """Send one 0x0C02 write, wait for the 0x8001 ack. Returns 'ok' | 'timeout' | 'poison'."""
    global _write_ack
    _write_ack = _loop.create_future()
    _send_frame(N.build_set(param, block, _next_serial()))
    try:
        a = await asyncio.wait_for(_write_ack, timeout=3.0)
        return "ok" if a.get("ok") else "fail"
    except asyncio.TimeoutError:
        return "poison" if not _mdvr_up else "timeout"
    finally:
        _write_ack = None


async def _do_write(changes):
    """Exclusive, paced write of ONLY the changed sections. `changes` = {param(str): {subkey: value}}.
    Per section: patch its last-read block, send the 0x0C02 SET, wait the ack; on a link drop retry
    once then skip on to the next. After each successful write the section is re-read so the UI shows
    the device's confirmed values. Emits per-section status so the browser can list OK / Failed."""
    global _cfg_busy
    _cfg_busy = True
    params = [int(p) for p in changes]                 # diff only — just the sections the user edited
    done, failed = 0, []
    log.info("WRITE  %d section(s): %s", len(params), ", ".join(hex(p) for p in params))
    await _broadcast({"type": "write_begin", "params": [p for p in params], "total": len(params)})
    try:
        for p in params:
            block = _cfg_raw.get(p)
            if not block:
                failed.append(p)
                log.warning("WRITE 0x%04X  SKIPPED — no prior read for this section", p)
                await _broadcast({"type": "write_item", "param": p, "status": "failed"})
                await _broadcast({"type": "write_progress", "done": done, "total": len(params)})
                continue
            blk = bytearray(block)
            requested = list(changes[str(p)].items())
            unmatched = [k for k, v in requested if not N.patch_block(p, blk, k, v)]
            applied = len(requested) - len(unmatched)
            # ── HARD SAFETY GATE ──────────────────────────────────────────────────────────
            # NEVER transmit a SET whose block size differs from the block we READ, and never
            # transmit if a requested field couldn't be placed (a layout/firmware mismatch).
            # A wrong-sized or mis-placed block is exactly what corrupts a device's config.
            if len(blk) != len(block):
                reason = "wrong data size (read %dB, built %dB)" % (len(block), len(blk))
            elif unmatched:
                reason = "field not applicable to this device: %s" % ", ".join(unmatched)
            else:
                reason = None
            if reason:
                failed.append(p)
                log.error("WRITE 0x%04X  ABORTED — %s (nothing sent)", p, reason)
                await _broadcast({"type": "write_item", "param": p, "status": "failed", "reason": reason})
                await _broadcast({"type": "write_progress", "done": done, "total": len(params)})
                continue
            # ──────────────────────────────────────────────────────────────────────────────
            if not _mdvr_up:
                await _await_up()
            r = await _write_one(p, bytes(blk))
            if r != "ok" and (r == "poison" or not _mdvr_up):
                await _await_up()                       # link dropped -> wait for reconnect, retry once
                r = await _write_one(p, bytes(blk))
            if r == "ok":
                _cfg_raw[p] = bytes(blk)                # trust the cache only after the device acked
                done += 1
                log.info("WRITE 0x%04X  %d field(s)  OK — re-reading", p, applied)
                await _read_section(p)                  # confirm + push fresh values to the browser
                await _broadcast({"type": "write_item", "param": p, "status": "ok"})
            else:
                failed.append(p)
                log.warning("WRITE 0x%04X  %s — skipping", p, r)
                await _broadcast({"type": "write_item", "param": p, "status": "failed",
                                  "reason": "device rejected / no ack" if r != "poison" else "link dropped"})
            await _broadcast({"type": "write_progress", "done": done, "total": len(params)})
    finally:
        _cfg_busy = False
        if _loop:
            await _broadcast(_mdvr_state_msg())         # re-sync link state after we release the device
    log.info("WRITE  done: %d/%d ok%s", done, len(params),
             (" (failed: %s)" % [hex(x) for x in failed]) if failed else "")
    await _broadcast({"type": "write_done", "done": done, "total": len(params),
                      "failed": [hex(x) for x in failed]})


async def _mdvr_loop():
    while True:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(MDVR_HOST, MDVR_PORT), timeout=4)
        except Exception:
            _set_state(False)
            await asyncio.sleep(2)
            continue
        pump = None
        try:
            writer.write(LOGIN_0C01); await writer.drain()
            data = await asyncio.wait_for(reader.read(4096), timeout=5)   # expect 0x8c01 login ack
            if not data:
                raise ConnectionError("no login reply")
            _set_state(True)
            _pending.clear()
            while not _tx_queue.empty():          # drop stale frames from a previous session
                _tx_queue.get_nowait()
            pump = asyncio.create_task(_tx_pump(writer))
            buf = bytearray(data)
            last_rx = _loop.time()               # device streams status ~1/s
            while True:
                for mid, ser, body in N.iter_messages(buf, _pending):
                    await _route(mid, ser, body)
                try:
                    chunk = await asyncio.wait_for(reader.read(4096), timeout=5)
                except asyncio.TimeoutError:
                    if _loop.time() - last_rx > 12:   # 12s silence = half-open/dead link -> reconnect
                        raise ConnectionError("no data — link dead")
                    continue
                if not chunk:
                    break
                last_rx = _loop.time()
                buf.extend(chunk)
        except Exception as e:
            log.warning("MDVR session ended: %s", e)
        finally:
            if pump:
                pump.cancel()
            _set_state(False)
            try:
                writer.close()
            except Exception:
                pass
            await asyncio.sleep(2)


# ── HTTP + WS ────────────────────────────────────────────────────────────────
async def index(request):
    return web.FileResponse(INDEX_HTML)


async def codec_js(request):
    name = request.match_info["name"]
    if name in _CODEC_JS and os.path.isfile(os.path.join(HERE, name)):
        return web.FileResponse(os.path.join(HERE, name), headers={"Content-Type": "application/javascript"})
    return web.Response(status=404)


async def ws_handler(request):
    ws = web.WebSocketResponse(heartbeat=None)     # browser reconnect + 1s status flow keep it alive
    await ws.prepare(request)
    _clients.add(ws)
    log.info("WS client connected (%d total)", len(_clients))
    await ws.send_str(json.dumps({"type": "mdvr", "state": "up" if _mdvr_up else "down",
                                  "host": MDVR_HOST, "port": MDVR_PORT, "ssid": AP_SSID}))
    await ws.send_str(json.dumps(_wifi_state_msg()))       # current WiFi so the browser can pick the right overlay
    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                log.info("WS cmd: %s", (msg.data or "")[:90])
                await _handle_ws_cmd(ws, msg.data)
            elif msg.type == WSMsgType.ERROR:
                log.warning("WS error: %s", ws.exception())
    finally:
        _clients.discard(ws)
        log.info("WS client disconnected (%d left)", len(_clients))
    return ws


async def _handle_ws_cmd(ws, data):
    """Settings actions from the browser -> frames on the MDVR socket."""
    try:
        cmd = json.loads(data)
    except Exception:
        return
    global _cfg_busy, _wifi_cur
    a = cmd.get("action")
    if a == "wifi_scan":                          # browser opened the WiFi picker -> give it the SSID list
        nets = await _wifi_scan()
        await ws.send_str(json.dumps({"type": "wifi_list", "networks": nets, "target": WIFI_SSID}))
        return
    if a == "wifi_connect":                        # user picked an SSID + password and hit Connect
        ssid = cmd.get("ssid", "")
        pw   = cmd.get("password", "")
        log.info("WiFi: connect request -> %r", ssid)
        ok, msg = await _wifi_do_connect(ssid, pw)
        _wifi_cur = await _current_ssid()
        await _broadcast(_wifi_state_msg())
        await ws.send_str(json.dumps({"type": "wifi_connect_result", "ok": ok, "msg": msg, "ssid": ssid}))
        return
    if a == "wifi_disconnect":                     # "Reconnect WiFi" -> drop the link, browser reopens the picker
        ok, msg = await _wifi_disconnect()
        _wifi_cur = await _current_ssid()
        await _broadcast(_wifi_state_msg())
        await ws.send_str(json.dumps({"type": "wifi_disconnected", "ok": ok, "msg": msg}))
        return
    if a == "play":                                # live video: remember quality + hand back the WS endpoint
        ch = int(cmd.get("channel", 0))
        mt = int(cmd.get("data_type", N.MEDIA_BOTH))   # 0 both / 1 video / 3 audio (same values as REQLIVE)
        bs = int(cmd.get("stream_type", N.MEDIA_MAIN)) # 0 main / 1 sub
        _media_req[ch] = ("live", mt, bs, None, None)
        await _media_ensure()
        await ws.send_str(json.dumps({"type": "endpoint", "imei": cmd.get("imei"), "channel": ch,
                                      "path": "/video/ch%d" % ch, "port": WEB_PORT}))
        return
    if a == "playback":                            # recorded video: 0xF8 VOD start for a time range
        ch = int(cmd.get("channel", 0))
        mt = int(cmd.get("data_type", N.MEDIA_BOTH))
        bs = int(cmd.get("stream_type", N.MEDIA_MAIN))
        _media_req[ch] = ("playback", mt, bs, cmd.get("start"), cmd.get("end"))
        await _media_ensure()
        await ws.send_str(json.dumps({"type": "pb_endpoint", "imei": cmd.get("imei"), "channel": ch,
                                      "path": "/video/ch%d" % ch, "port": WEB_PORT}))
        return
    if a == "resources":                           # clip list: 0x9205 on 9003 -> 0x1205 -> {clips}
        asyncio.create_task(_do_resources(ws, cmd.get("imei"), int(cmd.get("channel", 0)),
                                          cmd.get("start"), cmd.get("end")))
        return
    if a == "device_log":                          # device op/event log: 0x0C06 on 9003 -> 0x8C06 -> {entries}
        asyncio.create_task(_do_device_log(ws, cmd.get("imei"), cmd.get("start"), cmd.get("end"),
                                           int(cmd.get("log_type", 0)), int(cmd.get("page", 0)),
                                           int(cmd.get("size", 50))))
        return
    if a == "device_log_export":                   # save loaded log lines to a .txt next to config.py
        asyncio.create_task(_do_device_log_export(ws, cmd.get("imei"), cmd.get("entries", []) or [],
                                                  cmd.get("start"), cmd.get("end")))
        return
    if a == "pb_download":                         # capture the VOD range + mux to MP4 (like the OEM app)
        if _dl is not None:
            await ws.send_str(json.dumps({"type": "pb_dl_failed", "data": {"msg": "A download is already running"}}))
            return
        asyncio.create_task(_do_download(cmd.get("imei"), int(cmd.get("channel", 0)),
                                         cmd.get("start"), cmd.get("end"),
                                         int(cmd.get("expected", 0)), int(cmd.get("dur", 0))))
        return
    if a == "pb_download_cancel":                  # user hit Cancel -> stop the capture, drop partials
        if _dl is not None:
            _dl["cancel"] = True
        return
    if a in ("stop", "snapshot"):                  # live teardown via WS close; others no-op locally
        return
    if a in ("read_config", "write_config", "param_manage"):
        if not _mdvr_up:
            await ws.send_str(json.dumps({"type": "config_ack", "ok": False, "msg": "MDVR not connected"}))
            return
        if _cfg_busy:                             # a read/write already owns the device
            await ws.send_str(json.dumps({"type": "config_ack", "ok": False, "msg": "busy — read/write in progress"}))
            return
    if a == "read_config":                        # paced, exclusive
        params = [int(p) for p in cmd.get("params", [])]
        log.info("READ  request: %d section(s)", len(params))
        _cfg_busy = True                          # claim immediately to block races
        asyncio.create_task(_do_read(params))
    elif a == "write_config":                     # paced, exclusive, read-modify-write
        changes = cmd.get("changes", {}) or {}
        log.info("WRITE request: %d section(s)", len(changes))
        _cfg_busy = True
        asyncio.create_task(_do_write(changes))
    elif a == "param_manage":                     # 0=import 1=export 2=factory-reset
        code = int(cmd.get("code"))
        _send_frame(N.build_param_manage(code, _next_serial()))
        log.info("PARAM-MANAGE code=%d", code)


async def start():
    global _loop, _tx_queue
    _loop = asyncio.get_running_loop()
    _tx_queue = asyncio.Queue()
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/{name:richmor_codec_(?:jt808|ttx|native)\\.js}", codec_js)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/video/ch{ch:\\d+}", video_handler)   # live video WS (one per channel)
    app.router.add_get("/playbacks/{name}", playbacks_file)   # finished clip downloads (mp4)
    app.router.add_get("/logexports/{name}", logexport_file)  # exported device-log .txt files
    app.router.add_get("/clientlog", clientlog)               # browser JS errors -> richmor_config.log
    if os.path.isdir(ASSETS_DIR):
        app.router.add_static("/assets/", ASSETS_DIR)
    runner = web.AppRunner(app); await runner.setup()
    site = web.TCPSite(runner, WEB_HOST, WEB_PORT); await site.start()
    log.info("richmor_config up on %s:%d — MDVR target %s:%d (AP %s)", WEB_HOST, WEB_PORT, MDVR_HOST, MDVR_PORT, AP_SSID)
    if WIFI_MANAGE:
        log.info("WiFi service ON — browser-driven scan/connect; monitoring link state")
        asyncio.create_task(_wifi_monitor())
    asyncio.create_task(_mdvr_loop())
    asyncio.create_task(_provision_ffmpeg())   # ensure ffmpeg for clip download (background; cross-OS)
    while True:
        await asyncio.sleep(3600)


def run_server():
    """Run the aiohttp server on a private event loop. Callable from a background thread so the
    desktop wrapper (richmor_config_app.py / pywebview) can keep the main thread for its window."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(start())
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    try:
        asyncio.run(start())
    except KeyboardInterrupt:
        pass
