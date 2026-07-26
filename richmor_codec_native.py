#!/usr/bin/env python3
"""
richmor_codec_native.py — codec for the Richmor MDVR *native LAN* protocol (the "businessing"
protocol the OEM iVehicle app speaks directly to the recorder over its own WiFi AP, on 9003).

This is a SEPARATE protocol from the cloud stacks:
  * richmor_codec_jt808.py  — JT/T 808-SB the recorder speaks to the Globo360 server
  * richmor_codec_ttx.py    — the $dc TTX/T-protocol
  * THIS file               — the vendor LAN protocol on 10.10.10.1:9003 (config/control/status)

GROUND TRUTH: every offset below is transcribed from the iVehicle APK, class
`com.vehicle.app.businessing.message.response.GSensorResMessage.decode()` (and the msg-id table
in `...businessing.processor.iBusinessProtocol`). It is NOT reverse-engineered guesswork — it is
the app's own parser, verified byte-for-byte against a live capture of device #200000011999
(speed, DISK2 116/116 GB, 14 satellites, ACC, 11.1 V, accel, ICCID, IMEI all matched the app UI).

Framing is JT/T-808 style with 0x7e delimiters and 0x7d escaping:
    7e <msgid WORD> <attr WORD> <phone BCD6> <serial WORD> <body...> <xor> 7e
    on the wire:  7e -> 7d 02 ,  7d -> 7d 01     strings are GB2312, null-padded.

Message ids (from iBusinessProtocol):
    REQ_LOGIN      0x0C01 (3073)     app->dev   login (user "ceshi" / pass "123456")
    RSP_LOGIN      0x8C01 (35841)    dev->app   login ack
    REQ_HEART      0x0002 (2)        app->dev   heartbeat
    REQ_QUERY_PARM 0x0C03 (3075)     app->dev   read config block
    RSP_QUERY_PARM 0x8C03 (35843)    dev->app   config block
    REQ_SET_PARM   0x0C02 (3074)     app->dev   write config
    RSP_G_SENSOR / RSP_STATUS_INFO   0x8C08 (35848)  dev->app  status push (~1/s) — THIS file's decode
    REQ_LOCATION   0x8201 (33281)    app->dev   *** ask for GPS position ***
    RSP_LOCATION   0x0201 (513)      dev->app   *** GPS position reply (lat/lon/speed/heading) ***
    TRAP_LOCATION  0x0200 (512)      dev->app   GPS position trap/push

NOTE ON GPS: the 0x8C08 status block carries speed + satellite count + location *status*, but NO
lat/lon. Coordinates come from the separate REQ_LOCATION(0x8201) -> RSP_LOCATION(0x0201) exchange
(standard JT/T-808 0x0200 location body). That is why the app's "Data" screen shows speed/sats but
no numeric position, and works with no SIM/no internet — it is all local on 9003.
"""

MSG_LOGIN       = 0x0C01    # REQ_LOGIN
MSG_LOGIN_ACK   = 0x8C01    # RSP_LOGIN
MSG_HEARTBEAT   = 0x0002    # REQ_HEART
MSG_QUERY_PARM  = 0x0C03    # REQ_QUERY_PARM  (read config sections)
MSG_QUERY_ACK   = 0x8C03    # RSP_QUERY_PARM
MSG_SET_PARM    = 0x0C02    # REQ_SET_PARM    (write config)
MSG_PARAM_MANAGE = 0x0C07   # REQ_PARAM_MANAGE (save/reboot/reset)
MSG_DEVICE_LOG  = 0x0C06    # REQ_DEVICE_LOG
MSG_SCREEN_XY   = 0x0C05    # REQ_DEVICE_X_Y
MSG_REMOTE      = 0x0C04    # REQ_REMOTE_CONTROL / REMOTE_BUTTON
MSG_DISK_FORMAT = 0x0C08    # REQ_DISK_FORMAT
MSG_STATUS      = 0x8C08    # RSP_G_SENSOR / RSP_STATUS_INFO (pushed status block)
MSG_REQ_LOCATION = 0x8201   # ask for GPS position
MSG_RSP_LOCATION = 0x0201   # GPS position reply
MSG_TRAP_LOCATION = 0x0200  # GPS position trap/push
MSG_UNIVERSAL_ACK = 0x0001  # RSP_COMMON (client general response)
MSG_CAPTURE     = 0x8801    # REQ_CAPTURE (snapshot)
MSG_TEXT        = 0x8300    # REQ_TEXT (TTS/text)
MSG_MEDIA_LIST  = 0x9205    # REQ_MEDIA (recording list, 9008 side)

# config-section ids used with MSG_QUERY_PARM / MSG_SET_PARM (from iBusinessProtocol.PARAM_*)
PARAMS = {
    "power_manage": 0xF000, "date_language": 0xF001, "user_manage": 0xF002, "terminal_info": 0xF003,
    "function_switch": 0xF004, "central_platform": 0xF005, "local_setting": 0xF006, "net_3g4g": 0xF007,
    "wifi": 0xF008, "ipc": 0xF009, "ftp": 0xF00A, "io": 0xF00B, "speed": 0xF00C, "temperature": 0xF00D,
    "acceleration": 0xF00E, "voltage": 0xF00F, "motion_detection": 0xF010, "fuel_gauge": 0xF015,
    "speaker": 0xF016, "serial_port": 0xF017, "serial_main": 0xF018, "serial_ext": 0xF019,
    "record_basic": 0xF023, "record_encoding": 0xF024, "timed_recording": 0xF025, "disk_manage": 0xF026,
    "central_platform_config": 0xF027, "dsm": 0xF100, "adas": 0xF101, "bsd": 0xF102, "bsd2": 0xF103,
    "top_dsm": 0xF104, "cov": 0xF105, "adas_calibration": 0xF106, "bsd_calibration": 0xF107,
    "bsd2_calibration": 0xF108,
}

STRINGCODING = "gb2312"

# captured-verbatim frames that are known to work against the device
LOGIN_0C01_HEX     = "7e0c010020000000000000000163657368690000000000000000000000313233343536000000000000000000005f7e"
HEARTBEAT_0002_HEX = "7e000200000000000000000000ce7e"


# ── framing ──────────────────────────────────────────────────────────────────
def unescape(inner: bytes) -> bytes:
    """Reverse the on-wire 7d-escaping (7d02->7e, 7d01->7d) on a frame's inner bytes."""
    out = bytearray()
    i = 0
    while i < len(inner):
        if inner[i] == 0x7d and i + 1 < len(inner):
            out.append(0x7e if inner[i + 1] == 0x02 else 0x7d)
            i += 2
        else:
            out.append(inner[i])
            i += 1
    return bytes(out)


def iter_frames(buf: bytearray):
    """Yield raw inner (still-escaped) frame payloads from a stream buffer, mutating buf.
    A frame is the bytes between two 0x7e; the closing 0x7e is kept as the next opener."""
    while True:
        s = buf.find(0x7e)
        if s < 0:
            buf.clear()
            return
        e = buf.find(0x7e, s + 1)
        if e < 0:
            del buf[:s]
            return
        inner = bytes(buf[s + 1:e])
        del buf[:e]
        if inner:
            yield inner


def parse_header(frame_inner: bytes):
    """frame_inner is the (still-escaped) bytes between the 0x7e delimiters.
    Returns (msgid:int, serial:int, body:bytes) or None if malformed.
    NOTE: for a SUBPACKAGED frame this returns only that fragment's data — use
    iter_messages() when a section (e.g. central_platform, 1113 B) spans multiple frames."""
    b = unescape(frame_inner)
    if len(b) < 13:                       # 2+2+6+2 header + >=1 xor
        return None
    msgid  = int.from_bytes(b[0:2], "big")
    props  = int.from_bytes(b[2:4], "big")
    length = props & 0x3FF                  # low 10 bits = body length
    sub    = (props >> 13) & 1              # bit 13 = subpackage flag -> 16-byte header
    hlen   = 16 if sub else 12
    serial = int.from_bytes(b[10:12], "big")
    body   = b[hlen:hlen + length] if length else b[hlen:-1]
    return msgid, serial, body


def iter_messages(buf: bytearray, pending: dict = None):
    """Like iter_frames+parse_header but REASSEMBLES subpackaged messages. Yields
    (msgid, serial, full_body). Subpackages are grouped by (msgId, simNo) and ordered by
    packageNo 1..packageCount (mirrors MessagePackageGroupedBuffer). Non-subpackaged frames
    pass straight through. Pass a persistent `pending` dict to reassemble across TCP reads."""
    if pending is None:
        pending = {}
    for inner in iter_frames(buf):
        b = unescape(inner)
        if len(b) < 13:
            continue
        msgid  = int.from_bytes(b[0:2], "big")
        props  = int.from_bytes(b[2:4], "big")
        length = props & 0x3FF
        sub    = (props >> 13) & 1
        sim    = b[4:10].hex()
        serial = int.from_bytes(b[10:12], "big")
        if not sub:
            body = b[12:12 + length] if length else b[12:-1]
            yield msgid, serial, body
            continue
        count = int.from_bytes(b[12:14], "big")
        no    = int.from_bytes(b[14:16], "big")
        data  = b[16:16 + length]
        key   = (msgid, sim)
        d = pending.setdefault(key, {})
        d[no] = data
        if len(d) >= count and all(i in d for i in range(1, count + 1)):
            full = b"".join(d[i] for i in range(1, count + 1))
            del pending[key]
            yield msgid, serial, full


# ── frame BUILDER (mirror BusinessProtocolEncoder) ───────────────────────────
def _bcd(sim: str) -> bytes:
    """BCD-8421 encode the device/sim number to 6 bytes (left-padded to 12 digits)."""
    s = "".join(ch for ch in (sim or "") if ch.isdigit()).rjust(12, "0")[:12]
    return bytes((int(s[i]) << 4) | int(s[i + 1]) for i in range(0, 12, 2))


def _xor(data: bytes) -> int:
    x = 0
    for c in data:
        x ^= c
    return x


def _escape(data: bytes) -> bytes:
    out = bytearray()
    for c in data:
        if c == 0x7e:
            out += b"\x7d\x02"
        elif c == 0x7d:
            out += b"\x7d\x01"
        else:
            out.append(c)
    return bytes(out)


def _props(body_len: int, sub: bool = False) -> int:
    return (body_len & 0x3FF) | ((1 << 13) if sub else 0)


def build_frame(msgid: int, body: bytes = b"", sim: str = "00000000000", serial: int = 0) -> bytes:
    """Assemble a complete on-wire frame: 7e + escape(header+body+xor) + 7e.
    header = msgid(2) props(2) simBCD(6) serial(2)."""
    header = (msgid.to_bytes(2, "big") + _props(len(body)).to_bytes(2, "big")
              + _bcd(sim) + (serial & 0xFFFF).to_bytes(2, "big"))
    core = header + body
    core += bytes([_xor(core)])
    return b"\x7e" + _escape(core) + b"\x7e"


def _rpad(s: str, n: int) -> bytes:
    return (s or "").encode(STRINGCODING, "ignore")[:n].ljust(n, b"\x00")


# ── SECTION 1: Login (REQ_LOGIN 0x0C01 -> RSP_LOGIN 0x8C01) ──────────────────
def build_login(username: str = "ceshi", password: str = "123456",
                sim: str = "00000000000", serial: int = 0) -> bytes:
    """LoginReqMessage: body = username[16] + password[16] (GB2312, null-padded)."""
    body = _rpad(username, 16) + _rpad(password, 16)
    return build_frame(MSG_LOGIN, body, sim, serial)


def parse_login_ack(body: bytes) -> dict:
    """LoginResMessage.decode: success(1) + mediaPort(2, BE). success 0 = OK."""
    if len(body) < 3:
        return {}
    return {"success": body[0], "ok": body[0] == 0, "media_port": (body[1] << 8) | body[2]}


# =============================================================================
#  CONFIG FRAMEWORK  (query / set / param-manage / universal-ack)
#  transcribed from QuerySettingParamReqMessage / SetSettingParamReqMessage /
#  QuerySettingParamResMessage / ParamManageReqMessage / UniversalResMessage.
# =============================================================================
def _bcd2str(b: bytes) -> str:
    """BCD8421 bcd2String: each byte -> two decimal digits."""
    return "".join("%02x" % c for c in b)


def build_query(param_ids, serial: int = 0, sim: str = "00000000000") -> bytes:
    """REQ_QUERY_PARM 0x0C03 body = size(1) + N * paramId(4, BE)."""
    ids = list(param_ids)
    body = bytes([len(ids)]) + b"".join((p & 0xFFFFFFFF).to_bytes(4, "big") for p in ids)
    return build_frame(MSG_QUERY_PARM, body, sim, serial)


def build_set(param_id: int, block: bytes, serial: int = 0, sim: str = "00000000000") -> bytes:
    """REQ_SET_PARM 0x0C02 body = size(1)=1 + paramId(4, BE) + blockLen(2, BE) + block.
    `block` is the section's own encoded bytes (see SECTION_SET_LEN for the exact length)."""
    body = bytes([1]) + (param_id & 0xFFFFFFFF).to_bytes(4, "big") + (len(block) & 0xFFFF).to_bytes(2, "big") + block
    return build_frame(MSG_SET_PARM, body, sim, serial)


def build_param_manage(param: int, serial: int = 0, sim: str = "00000000000") -> bytes:
    """REQ_PARAM_MANAGE 0x0C07 body = param(4, BE). param 0=import, 1=export, 2=factory-reset."""
    return build_frame(MSG_PARAM_MANAGE, (param & 0xFFFFFFFF).to_bytes(4, "big"), sim, serial)


PARAM_MANAGE_IMPORT  = 0
PARAM_MANAGE_EXPORT  = 1
PARAM_MANAGE_FACTORY = 2


# =============================================================================
#  MEDIA (real-time video on 9008) — Richmor JT1078 "01cd", device-AP direct mode.
#  Transcribed from the APK MediaProtocolEncoder/Decoder + LiveMediaRequestMessage.
#  On-wire header (32 B): 30 31 63 64 | msgId(1) | 00 00 00 | simBCD(6) | ch(1) |
#                         00*13 | bodyLen(4, BE) | body
#  Incoming frame (magic INCLUDED): [4]magic [5]M/PT [6-7]seq [8-13]simBCD [14]ch
#    [15]=(dataType<<4 | subpkg) [16-23]ts(8) [video:24-27 intervals] len(4,BE) payload
# =============================================================================
MEDIA_FLAG      = b"\x30\x31\x63\x64"     # "01cd" — frame delimiter / header magic
MEDIA_REQLIVE   = 0xFD                     # start real-time video
MEDIA_CTRL      = 0xFB                     # control (stop / change stream)
MEDIA_KEEPALIVE = 0xFC                     # header-only keep-alive
MEDIA_BOTH, MEDIA_VIDEO, MEDIA_TALK, MEDIA_AUDIO = 0, 1, 2, 3   # mediaType (body[1])
MEDIA_MAIN, MEDIA_SUB = 0, 1                                    # bitStream (body[2])
_MEDIA_VIDEO_TYPES = (0, 1, 2)             # incoming dataType hi-nibble: 0/1/2 video (I/P/B)
MEDIA_DT_I, MEDIA_DT_AUDIO = 0, 3
MEDIA_SP_ATOM, MEDIA_SP_FIRST, MEDIA_SP_LAST, MEDIA_SP_MIDDLE = 0, 1, 2, 3  # subpackage lo-nibble


def _media_header(msgid: int, body: bytes = b"", sim: str = "0", channel: int = 0) -> bytes:
    return (MEDIA_FLAG + bytes([msgid & 0xFF]) + b"\x00\x00\x00" + _bcd(sim)
            + bytes([channel & 0xFF]) + b"\x00" * 13 + len(body).to_bytes(4, "big") + body)


def build_media_reqlive(channel: int, media_type: int = MEDIA_BOTH,
                        bit_stream: int = MEDIA_MAIN, sim: str = "0") -> bytes:
    """REQLIVE 0xFD: body = channel, mediaType, bitStream, vgaRes(=2), vgaEnc(=0)."""
    return _media_header(MEDIA_REQLIVE,
                         bytes([channel & 0xFF, media_type & 0xFF, bit_stream & 0xFF, 2, 0]),
                         sim, channel)


def build_media_stop(channel: int, close_type: int = 0, bit_stream: int = 0, sim: str = "0") -> bytes:
    """CTRL 0xFB: body = channel, command(0=CLOSEMEDIA), closeType(0=all/1=audio/2=video), bitStream."""
    return _media_header(MEDIA_CTRL, bytes([channel & 0xFF, 0, close_type & 0xFF, bit_stream & 0xFF]),
                         sim, channel)


def build_media_keepalive(sim: str = "0") -> bytes:
    return _media_header(MEDIA_KEEPALIVE, b"", sim, 0)


class MediaFramer:
    """Length-aware reassembler for the 9008 '01cd' stream. feed(bytes) -> list of
    {channel, data_type, subpkg, payload}. Advances by the declared length (not by
    delimiter search) so a payload that happens to contain the magic can't mis-frame."""
    def __init__(self):
        self.buf = bytearray()

    def feed(self, data: bytes):
        self.buf += data
        out = []
        while True:
            i = self.buf.find(MEDIA_FLAG)
            if i < 0:
                if len(self.buf) > 3:
                    del self.buf[:len(self.buf) - 3]      # keep a possible partial-magic tail
                break
            if i:
                del self.buf[:i]                          # drop junk before the magic
            if len(self.buf) < 16:                        # need up to byte 15 (type/subpkg)
                break
            nib = self.buf[15]
            dtype, sub = (nib >> 4) & 0xF, nib & 0xF
            if dtype in _MEDIA_VIDEO_TYPES:
                if len(self.buf) < 32:
                    break
                length, poff = int.from_bytes(self.buf[28:32], "big"), 32
            else:
                if len(self.buf) < 28:
                    break
                length, poff = int.from_bytes(self.buf[24:28], "big"), 28
            total = poff + length
            if len(self.buf) < total:
                break
            out.append({"channel": self.buf[14], "data_type": dtype, "subpkg": sub,
                        "payload": bytes(self.buf[poff:total])})
            del self.buf[:total]
        return out


# ── PLAYBACK: clip list (0x9205/0x1205 on 9003) + VOD start/control (0xF8/0xF7 on 9008) ──
MSG_MEDIA_LIST_ACK = 0x1205        # RSP_MEDIA (recorded-clip list reply)
MEDIA_PLAYBACK      = 0xF8         # REQPLAYBACK  (start VOD, on 9008)
MEDIA_PLAYBACK_CTRL = 0xF7         # CTRLPLAYBACK (pause/resume/stop/ff, on 9008)
VOD_BEGIN, VOD_PAUSE, VOD_STOP, VOD_FF = 0, 1, 2, 3   # VodPlayType


def _bcd12(dec: str) -> bytes:
    """12 decimal digits -> 6 BCD bytes."""
    s = "".join(ch for ch in dec if ch.isdigit()).rjust(12, "0")[:12]
    return bytes((int(s[i]) << 4) | int(s[i + 1]) for i in range(0, 12, 2))


def _dt2bcd(x: str) -> bytes:
    """'YYYY-MM-DD HH:MM:SS' (or any 14-digit stamp) -> YYMMDDhhmmss BCD (6 B, century dropped)."""
    d = "".join(ch for ch in x if ch.isdigit())        # YYYYMMDDHHMMSS
    return _bcd12(d[2:14] if len(d) >= 14 else d)       # drop the century -> YYMMDDhhmmss


def _bcd2dt(b: bytes) -> str:
    """6 BCD bytes YYMMDDhhmmss -> 'YYYY-MM-DD HH:MM:SS' (assumes 20YY)."""
    d = "".join("%02x" % x for x in b[:6]).ljust(12, "0")
    return "20%s-%s-%s %s:%s:%s" % (d[0:2], d[2:4], d[4:6], d[6:8], d[8:10], d[10:12])


def build_media_query(channel: int, begin: str, end: str, alarm: bytes = b"\x00" * 8,
                      media_type: int = 0, bit_type: int = 0, storage: int = 0,
                      serial: int = 0, sim: str = "00000000000") -> bytes:
    """REQ_MEDIA 0x9205 (9003 businessing): channel, begin BCD(6), end BCD(6), alarmFlag(8),
    mediaType(1), bitType(1), storage(1)."""
    body = (bytes([channel & 0xFF]) + _dt2bcd(begin) + _dt2bcd(end)
            + alarm[:8].ljust(8, b"\x00") + bytes([media_type & 0xFF, bit_type & 0xFF, storage & 0xFF]))
    return build_frame(MSG_MEDIA_LIST, body, sim, serial)


def parse_media_list(body: bytes) -> dict:
    """RSP_MEDIA 0x1205: [2 prefix][count u32][records...], each record 28 B."""
    if len(body) < 6:
        return {"clips": []}
    n = _u32(body, 2)
    clips, off = [], 6
    for _ in range(n):
        r = body[off:off + 28]
        if len(r) < 28:
            break
        clips.append({"channel": r[0], "start": _bcd2dt(r[1:7]), "end": _bcd2dt(r[7:13]),
                      "media_type": r[21], "bit_type": r[22], "storage": r[23], "size": _u32(r, 24)})
        off += 28
    return {"clips": clips}


def build_media_playback(channel: int, begin: str, end: str, media_type: int = MEDIA_BOTH,
                         bit_stream: int = MEDIA_MAIN, storage: int = 0, play_type: int = 0,
                         play_times: int = 0, sim: str = "0") -> bytes:
    """REQPLAYBACK 0xF8 (9008): channel, mediaType, bitStream, storage, playType, playTimes,
    begin BCD(6), end BCD(6)."""
    body = (bytes([channel & 0xFF, media_type & 0xFF, bit_stream & 0xFF, storage & 0xFF,
                   play_type & 0xFF, play_times & 0xFF]) + _dt2bcd(begin) + _dt2bcd(end))
    return _media_header(MEDIA_PLAYBACK, body, sim, channel)


def build_media_playback_ctrl(channel: int, play_type: int, play_times: int = 1,
                              drag_time: str = "", sim: str = "0") -> bytes:
    """CTRLPLAYBACK 0xF7 (9008): channel, playType, playTimes, [dragTime BCD(6)]."""
    body = bytes([channel & 0xFF, play_type & 0xFF, play_times & 0xFF])
    if drag_time:
        body += _dt2bcd(drag_time)
    return _media_header(MEDIA_PLAYBACK_CTRL, body, sim, channel)


# ── DEVICE LOG (operation/event log): REQ 0x0C06 -> RSP 0x8C06 (paged, time-ranged) ──
MSG_DEVICE_LOG_ACK = 0x8C06


def build_device_log(start: str, end: str, log_type: int = 0, page: int = 0, size: int = 0,
                     serial: int = 0, sim: str = "00000000000") -> bytes:
    """REQ_DEVICE_LOG 0x0C06: startTime BCD(6), endTime BCD(6), type(1), page(1), size(1)."""
    body = _dt2bcd(start) + _dt2bcd(end) + bytes([log_type & 0xFF, page & 0xFF, size & 0xFF])
    return build_frame(MSG_DEVICE_LOG, body, sim, serial)


def parse_device_log(body: bytes) -> dict:
    """RSP_DEVICE_LOG 0x8C06: logNum(4 BE) + N entries of [time BCD(6)][len(2 BE)][body(len, GB2312)]."""
    if len(body) < 4:
        return {"count": 0, "entries": []}
    n = _u32(body, 0)
    entries, off = [], 4
    for _ in range(n):
        if off + 8 > len(body):
            break
        t = _bcd2dt(body[off:off + 6])
        ln = _u16(body, off + 6)
        raw = body[off + 8:off + 8 + ln]
        text = raw.decode("gbk", "ignore").replace("\x00", "").strip()   # APK uses GB2312; gbk is a superset
        entries.append({"time": t, "text": text, "len": ln})
        off += 8 + ln
    return {"count": n, "entries": entries}


def parse_query_response(body: bytes) -> dict:
    """Split a RSP_QUERY_PARM 0x8C03 body. Per the app it is decoded at offset 3:
    [3 bytes prefix][paramId(4)][size(2)][block...]. Returns {param_id, section, fields}."""
    if len(body) < 9:
        return {}
    off = 3
    param_id = _u32(body, off)
    size = _u16(body, off + 4)
    block = body[off + 6:]
    name, dec = _SECTIONS.get(param_id, (None, None))
    out = {"param_id": param_id, "size": size, "section": name, "block_hex": block.hex()}
    if dec:
        try:
            out["fields"] = dec(block)
        except Exception as e:
            out["error"] = str(e)
    return out


def parse_universal_ack(body: bytes) -> dict:
    """UniversalResMessage.decode: no(2) + id(2) + result(1). result 0 = success."""
    if len(body) < 5:
        return {}
    return {"serial": _u16(body, 0), "msg_id": _u16(body, 2), "result": body[4], "ok": body[4] == 0}


# ── section sub-decoders ─────────────────────────────────────────────────────
def _sub_speed(b, i):   # SpeedSubResMessage (7 bytes)
    return {"switch": b[i], "threshold": _u16(b, i + 1), "duration": _u16(b, i + 3),
            "video_linkage": b[i + 5], "alarm_linkage": b[i + 6]}

def _sub_io(b, i):      # IOSettingSubResMessage (9 bytes)
    return {"io_type": _u16(b, i), "trigger_level": b[i + 2], "delay": b[i + 3],
            "video_linkage": b[i + 4], "alarm_linkage": b[i + 5], "preview_linkage": b[i + 6],
            "holding_time": _u16(b, i + 7)}

def _sub_center(b, i):  # CentralPlatformSubResMessage (138 bytes) — the 8 server slots
    return {"enabled": b[i], "server_type": b[i + 1], "main_port": _u32(b, i + 2),
            "sub_port": _u32(b, i + 6), "main_ip": _str(b, i + 10, 64), "sub_ip": _str(b, i + 74, 64)}


# ── section decoders (each parses the section block from a 0x8C03 reply) ──────
def dec_power_manage(b):
    return {"power_model": b[0], "multi_screen": b[1], "screensaver_delay": _u32(b, 2),
            "shutdown_delay": _u32(b, 6), "boot_time": _bcd2str(b[10:13]), "shutdown_time": _bcd2str(b[13:16]),
            "channel": b[16] if len(b) > 16 else None}

def dec_date_language(b):
    return {"time_format": b[0], "time_sync": b[1], "timeout": b[2], "language": b[3], "time_zone": b[4],
            "date": _bcd2str(b[5:9]), "time": _bcd2str(b[9:12])}

def dec_user_manage(b):
    return {"password_enabled": b[0], "admin_password": _str(b, 1, 8), "user_password": _str(b, 9, 8)}

def dec_terminal_info(b):
    o = {"device_no": _str(b, 0, 20), "phone": _str(b, 20, 20), "plate": _str(b, 40, 20),
         "plate_color": _str(b, 60, 10), "device_type": _str(b, 70, 20), "province_id": _str(b, 90, 4),
         "city_id": _str(b, 94, 6), "frame_no": _str(b, 100, 20), "date": _str(b, 120, 12),
         "company": _str(b, 132, 20), "service_line": _str(b, 152, 20), "terminal_model": _str(b, 172, 36),
         "vendor_id": _str(b, 208, 20), "terminal_id": _str(b, 228, 36)}
    if len(b) > 264:
        o["cccid"] = _str(b, 264, 20)
        o["locomotive_no"] = _str(b, 284, 24)
    return o

def dec_function_switch(b):
    return {"security_voice": b[0], "start_snapshot": b[1], "standard_switch": b[2],
            "positioning_assist": b[3], "startup_tone": b[4], "location_mode": b[5],
            "su_standard_upload": b[6], "lcd_backlight": b[7], "ntrip_port": _u32(b, 16),
            "ntrip_ip": _str(b, 20, 64), "ntrip_user": _str(b, 84, 32),
            "ntrip_password": _str(b, 116, 32), "ntrip_mount_point": _str(b, 148, 32)}

def dec_central_platform(b):
    o = {"server_type": b[0], "servers": [_sub_center(b, i * 138 + 1) for i in range(8)]}
    if len(b) > 1112:
        o["attachment_upload"] = [b[1105 + i] for i in range(8)]
    return o

def dec_local_setting(b):
    return {"connect_type": b[0], "ip": _str(b, 1, 20), "mask": _str(b, 21, 20), "gateway": _str(b, 41, 20),
            "dns1": _str(b, 61, 20), "dns2": _str(b, 81, 20), "mac": _str(b, 101, 20)}

def dec_net_3g4g(b):
    o = {"enable": b[0], "type": b[1], "auth_mode": b[2], "private_dialing": b[3], "apn": _str(b, 4, 40),
         "center_number": _str(b, 44, 40), "sms_center": _str(b, 84, 24), "username": _str(b, 108, 40),
         "password": _str(b, 148, 40)}
    if len(b) > 188:
        o["search_mode"] = b[188]
    return o

def dec_wifi(b):
    # firmware variant: when block >=198 there is an extra 'type' byte shifting the AP fields by 1
    base = 5 if len(b) >= 198 else 4
    o = {"encryption_switch": b[0], "auth_mode": b[1], "encryption_type": b[2], "dhcp": b[3]}
    if len(b) >= 198:
        o["type"] = b[4]
    o.update({"ap_ssid": _str(b, base, 20), "ap_password": _str(b, base + 20, 16),
              "ap_ip": _str(b, base + 36, 20), "ap_gateway": _str(b, base + 56, 20),
              "ap_mask": _str(b, base + 76, 20), "station_ssid": _str(b, base + 96, 20),
              "station_password": _str(b, base + 116, 16), "station_ip": _str(b, base + 132, 20),
              "station_gateway": _str(b, base + 152, 20), "station_mask": _str(b, base + 172, 20)})
    return o

def dec_ftp(b):
    return {"ftp_port": _u32(b, 0), "ftp_ip": _str(b, 0, 64), "ftp_user": _str(b, 0, 20), "ftp_password": _str(b, 0, 20)}

def dec_io(b):
    return {"channels": [_sub_io(b, i * 9) for i in range(16)]}

def dec_speed(b):
    return {"source": b[0], "unit": b[1], "limit_type": b[2], "night_limit": b[3], "pulse_factor": _u32(b, 4),
            "driven_distance": _u32(b, 8), "limit_value": _u16(b, 12), "start_time": _bcd2str(b[14:17]),
            "end_time": _bcd2str(b[17:20]), "levels": [_sub_speed(b, i * 7 + 20) for i in range(5)]}

def dec_temperature(b):
    return {"unit": b[0], "levels": [_sub_speed(b, i * 7 + 1) for i in range(2)]}

def dec_voltage(b):
    return {"delay_shutdown": b[0], "levels": [_sub_speed(b, i * 7 + 1) for i in range(2)]}

def dec_acceleration(b):
    return {"calibrated": b[0], "levels": [_sub_speed(b, i * 7 + 1) for i in range(5)]}

def dec_motion_detection(b):
    n = b[8]
    return {"alarm_interval": b[0], "snap_switch": b[1], "snapshots": _u16(b, 2),
            "capture_interval": _u32(b, 4), "channel_num": n}

def dec_fuel_gauge(b):
    return {"tank_capacity": _u32(b, 0)}

def dec_speaker(b):
    return {"speakers": [{"priority": b[i * 3], "channel": b[i * 3 + 1], "speaker": b[i * 3 + 2]} for i in range(7)]}

def dec_record_basic(b):
    return {"video_format": b[0], "video_model": b[1], "audio_gain": b[2], "alarm_pre_record": _u32(b, 3),
            "alarm_delay": _u32(b, 7), "alarm_file_protect": b[11], "display_resolution": b[12],
            "osd_overlay": _u32(b, 13)}

def dec_disk_manage(b):
    return {"disks": [{"video_use": b[i * 2], "priority": b[i * 2 + 1]} for i in range(4)]}

def dec_timed_recording(b):
    return {"slots": [{"raw": b[i * 12:i * 12 + 12].hex()} for i in range(8)]}

def dec_dsm(b):
    return {"enable": b[0], "alarm_video": b[1], "debug_mode": b[2], "snap_enable": b[3],
            "channel": b[4], "delay": _u32(b, 5), "duration": _u32(b, 9), "l1_speed": b[13], "l2_speed": b[14],
            "fatigue": b[15], "fatigue_threshold": _u32(b, 16), "fatigue_interval": _u16(b, 20), "yawn": b[22],
            "eyes_closed": b[23], "smoke": b[24], "smoke_threshold": _u32(b, 25), "smoke_interval": _u16(b, 29),
            "phone": b[31], "call_threshold": _u32(b, 32), "call_interval": _u16(b, 36), "distraction": b[38],
            "distraction_threshold": _u32(b, 39), "distraction_interval": _u16(b, 43), "look_left": b[45],
            "look_right": b[46], "head_up": b[47], "head_down": b[48], "driver_abnormal": b[49],
            "abnormal_threshold": _u32(b, 50), "abnormal_interval": _u16(b, 54), "no_face": b[56],
            "off_seat": b[57], "sunglasses": b[58], "mouth_occlusion": b[59], "shield_interval": _u16(b, 60)}

def dec_adas(b):
    return {"enable": b[0], "alarm_video": b[1], "channel": b[2], "snap_enable": b[3], "delay": _u32(b, 4),
            "duration": _u32(b, 8), "report_interval": _u16(b, 12), "l1_speed": b[14], "l2_speed": b[15],
            "left_ldw": b[16], "right_ldw": b[17], "ldw_threshold": _u32(b, 18), "fcw": b[22],
            "fcw_threshold": _u32(b, 23), "pcw": b[27], "pcw_threshold": _u32(b, 28), "hmw": b[32],
            "hmw_threshold": _u32(b, 33)}

def dec_bsd(b):
    return {"enable": b[0], "alarm_video": b[1], "curb_detection": b[2], "channel": b[3], "snap_enable": b[4],
            "delay": _u32(b, 5), "duration": _u32(b, 9), "preview_switch": b[13], "blind_area_attr": b[14]}

def dec_cov(b):
    return {"enable": b[0], "channel": b[1], "accuracy": _u32(b, 2), "inhibition_time": _u32(b, 6),
            "cycle": _u32(b, 10)}

def dec_top_dsm(b):
    return {"enable": b[0], "alarm_video": b[1], "channel": b[2], "snap_enable": b[3], "delay": b[4],
            "duration": b[5], "alarm_speed": b[6], "seatbelt": b[7], "phone_play": b[8], "wheel_off": b[9],
            "unbelt_threshold": _u32(b, 10), "phone_threshold": _u32(b, 14), "wheel_off_threshold": _u32(b, 18),
            "unbelt_interval": _u16(b, 22), "phone_interval": _u16(b, 24), "wheel_off_interval": _u16(b, 26)}


# ── SECTION registry: param_id -> (name, decoder) ; set-block length for build_set
_SECTIONS = {
    PARAMS["power_manage"]:      ("power_manage",      dec_power_manage),
    PARAMS["date_language"]:     ("date_language",     dec_date_language),
    PARAMS["user_manage"]:       ("user_manage",       dec_user_manage),
    PARAMS["terminal_info"]:     ("terminal_info",     dec_terminal_info),
    PARAMS["function_switch"]:   ("function_switch",   dec_function_switch),
    PARAMS["central_platform"]:  ("central_platform",  dec_central_platform),
    PARAMS["local_setting"]:     ("local_setting",     dec_local_setting),
    PARAMS["net_3g4g"]:          ("net_3g4g",          dec_net_3g4g),
    PARAMS["wifi"]:              ("wifi",              dec_wifi),
    PARAMS["ftp"]:               ("ftp",               dec_ftp),
    PARAMS["io"]:                ("io",                dec_io),
    PARAMS["speed"]:             ("speed",             dec_speed),
    PARAMS["temperature"]:       ("temperature",       dec_temperature),
    PARAMS["voltage"]:           ("voltage",           dec_voltage),
    PARAMS["acceleration"]:      ("acceleration",      dec_acceleration),
    PARAMS["motion_detection"]:  ("motion_detection",  dec_motion_detection),
    PARAMS["fuel_gauge"]:        ("fuel_gauge",        dec_fuel_gauge),
    PARAMS["speaker"]:           ("speaker",           dec_speaker),
    PARAMS["record_basic"]:      ("record_basic",      dec_record_basic),
    PARAMS["disk_manage"]:       ("disk_manage",       dec_disk_manage),
    PARAMS["timed_recording"]:   ("timed_recording",   dec_timed_recording),
    PARAMS["dsm"]:               ("dsm",               dec_dsm),
    PARAMS["adas"]:              ("adas",              dec_adas),
    PARAMS["bsd"]:               ("bsd",               dec_bsd),
    PARAMS["bsd2"]:              ("bsd2",              dec_bsd),
    PARAMS["cov"]:               ("cov",               dec_cov),
    PARAMS["top_dsm"]:           ("top_dsm",           dec_top_dsm),
}

# exact SET block length per section (from SetSettingParamReqMessage) — for build_set validation
SECTION_SET_LEN = {
    PARAMS["power_manage"]: 16, PARAMS["date_language"]: 12, PARAMS["user_manage"]: 17,
    PARAMS["terminal_info"]: 264, PARAMS["function_switch"]: 5, PARAMS["central_platform"]: 1113,
    PARAMS["local_setting"]: 121, PARAMS["net_3g4g"]: 188, PARAMS["wifi"]: 196, PARAMS["io"]: 144,
    PARAMS["speed"]: 55, PARAMS["temperature"]: 15, PARAMS["acceleration"]: 36, PARAMS["voltage"]: 15,
    PARAMS["fuel_gauge"]: 4, PARAMS["speaker"]: 21, PARAMS["record_basic"]: 17, PARAMS["timed_recording"]: 96,
    PARAMS["disk_manage"]: 8, PARAMS["dsm"]: 62, PARAMS["adas"]: 37, PARAMS["bsd"]: 15, PARAMS["bsd2"]: 15,
    PARAMS["top_dsm"]: 28, PARAMS["cov"]: 14,
}


# =============================================================================
#  FIELD-LEVEL read/patch  (per-field offset/width/kind) — for the config UI:
#  read_fields() -> flat {key: value};  patch_block() = read-modify-write a field.
#  Offsets/widths/kinds transcribed 1:1 from the section decoders above. An offset
#  may be a callable(block_len) for WiFi (its AP/station fields shift by a base byte).
#  kinds: u8 / u16 / u32 (unsigned BE ints), str (GB2312 null-padded), bcd (2 digits/byte).
# =============================================================================
def _wb(d):
    """WiFi base-relative offset: base is 5 if the block is the newer >=198-byte variant else 4."""
    return lambda n: (5 if n >= 198 else 4) + d

_SPEED_SUB = {"switch": (0, 1, "u8"), "threshold": (1, 2, "u16"), "duration": (3, 2, "u16"),
              "video_linkage": (5, 1, "u8"), "alarm_linkage": (6, 1, "u8")}

FIELD_META = {
    0xF000: {"scalar": {"power_model": (0, 1, "u8"), "multi_screen": (1, 1, "u8"),
        "screensaver_delay": (2, 4, "u32"), "shutdown_delay": (6, 4, "u32"),
        "boot_time": (10, 3, "bcd"), "shutdown_time": (13, 3, "bcd"), "channel": (16, 1, "u8")}},
    0xF001: {"scalar": {"time_format": (0, 1, "u8"), "time_sync": (1, 1, "u8"), "timeout": (2, 1, "u8"),
        "language": (3, 1, "u8"), "time_zone": (4, 1, "u8"), "date": (5, 4, "bcd"), "time": (9, 3, "bcd")}},
    0xF002: {"scalar": {"password_enabled": (0, 1, "u8"), "admin_password": (1, 8, "str"), "user_password": (9, 8, "str")}},
    0xF003: {"scalar": {"device_no": (0, 20, "str"), "phone": (20, 20, "str"), "plate": (40, 20, "str"),
        "plate_color": (60, 10, "str"), "device_type": (70, 20, "str"), "province_id": (90, 4, "str"),
        "city_id": (94, 6, "str"), "frame_no": (100, 20, "str"), "date": (120, 12, "str"),
        "company": (132, 20, "str"), "service_line": (152, 20, "str"), "terminal_model": (172, 36, "str"),
        "vendor_id": (208, 20, "str"), "terminal_id": (228, 36, "str"), "cccid": (264, 20, "str"),
        "locomotive_no": (284, 24, "str")}},
    0xF004: {"scalar": {"security_voice": (0, 1, "u8"), "start_snapshot": (1, 1, "u8"), "standard_switch": (2, 1, "u8"),
        "positioning_assist": (3, 1, "u8"), "startup_tone": (4, 1, "u8"), "location_mode": (5, 1, "u8"),
        "su_standard_upload": (6, 1, "u8"), "lcd_backlight": (7, 1, "u8"), "ntrip_port": (16, 4, "u32"),
        "ntrip_ip": (20, 64, "str"), "ntrip_user": (84, 32, "str"), "ntrip_password": (116, 32, "str"),
        "ntrip_mount_point": (148, 32, "str")}},
    0xF005: {"scalar": {"server_type": (0, 1, "u8")},
             "slot": {"base": 1, "stride": 138, "count": 8, "fields": {
        "enabled": (0, 1, "u8"), "server_type": (1, 1, "u8"), "main_port": (2, 4, "u32"),
        "sub_port": (6, 4, "u32"), "main_ip": (10, 64, "str"), "sub_ip": (74, 64, "str")}}},
    0xF006: {"scalar": {"connect_type": (0, 1, "u8"), "ip": (1, 20, "str"), "mask": (21, 20, "str"),
        "gateway": (41, 20, "str"), "dns1": (61, 20, "str"), "dns2": (81, 20, "str"), "mac": (101, 20, "str")}},
    0xF007: {"scalar": {"enable": (0, 1, "u8"), "type": (1, 1, "u8"), "auth_mode": (2, 1, "u8"),
        "private_dialing": (3, 1, "u8"), "apn": (4, 40, "str"), "center_number": (44, 40, "str"),
        "sms_center": (84, 24, "str"), "username": (108, 40, "str"), "password": (148, 40, "str"),
        "search_mode": (188, 1, "u8")}},
    0xF008: {"scalar": {"encryption_switch": (0, 1, "u8"), "auth_mode": (1, 1, "u8"), "encryption_type": (2, 1, "u8"),
        "dhcp": (3, 1, "u8"), "type": (4, 1, "u8"),
        "ap_ssid": (_wb(0), 20, "str"), "ap_password": (_wb(20), 16, "str"), "ap_ip": (_wb(36), 20, "str"),
        "ap_gateway": (_wb(56), 20, "str"), "ap_mask": (_wb(76), 20, "str"), "station_ssid": (_wb(96), 20, "str"),
        "station_password": (_wb(116), 16, "str"), "station_ip": (_wb(132), 20, "str"),
        "station_gateway": (_wb(152), 20, "str"), "station_mask": (_wb(172), 20, "str")}},
    0xF00A: {"scalar": {"ftp_port": (0, 4, "u32"), "ftp_ip": (4, 64, "str"), "ftp_user": (68, 20, "str"),
        "ftp_password": (88, 20, "str")}},   # sequential layout (the APK decoder had a copy/paste offset bug)
    # IO: 16 fixed channels. Per-slot size = len/16, derived from the packet (9B on old firmware,
    # 12B once EvidenceChn/EvidenceSnap/EvidenceRecord were added). Known fields sit at 0..8; any
    # extra bytes are left untouched and preserved verbatim on write.
    0xF00B: {"slot": {"base": 0, "stride": (lambda b: (len(b) // 16) if b else 9), "count": 16, "fields": {
        "io_type": (0, 2, "u16"), "trigger_level": (2, 1, "u8"), "delay": (3, 1, "u8"),
        "video_linkage": (4, 1, "u8"), "alarm_linkage": (5, 1, "u8"), "preview_linkage": (6, 1, "u8"),
        "holding_time": (7, 2, "u16"),
        # 12-byte firmware only — bounded to the actual stride so they're skipped on 9-byte rows
        "evidence_channel": (9, 1, "u8"), "forensic_capture": (10, 1, "u8"), "forensic_video": (11, 1, "u8")}}},
    0xF00C: {"scalar": {"source": (0, 1, "u8"), "unit": (1, 1, "u8"), "limit_type": (2, 1, "u8"),
        "night_limit": (3, 1, "u8"), "pulse_factor": (4, 4, "u32"), "driven_distance": (8, 4, "u32"),
        "limit_value": (12, 2, "u16"), "start_time": (14, 3, "bcd"), "end_time": (17, 3, "bcd")},
             "slot": {"base": 20, "stride": 7, "count": 5, "fields": dict(_SPEED_SUB)}},
    0xF00D: {"scalar": {"unit": (0, 1, "u8")}, "slot": {"base": 1, "stride": 7, "count": 2, "fields": dict(_SPEED_SUB)}},
    # Acceleration/G-sensor: 5 fixed axes. Per-slot size = (len-1)/5, derived from the packet
    # (7B old, 10B once EChn/ESnap/ERec/WarnT were added). Extra bytes preserved on write.
    0xF00E: {"scalar": {"calibrated": (0, 1, "u8")},
             "slot": {"base": 1, "stride": (lambda b: ((len(b) - 1) // 5) if b and len(b) > 1 else 7),
                      "count": 5, "fields": dict(_SPEED_SUB)}},
    0xF00F: {"scalar": {"delay_shutdown": (0, 1, "u8")}, "slot": {"base": 1, "stride": 7, "count": 2, "fields": dict(_SPEED_SUB)}},
    0xF010: {"scalar": {"alarm_interval": (0, 1, "u8"), "snap_switch": (1, 1, "u8"), "snapshots": (2, 2, "u16"),
        "capture_interval": (4, 4, "u32"), "channel_num": (8, 1, "u8")}},
    0xF015: {"scalar": {"tank_capacity": (0, 4, "u32")}},
    0xF016: {"slot": {"base": 0, "stride": 3, "count": 7, "fields": {
        "priority": (0, 1, "u8"), "channel": (1, 1, "u8"), "speaker": (2, 1, "u8")}}},
    0xF023: {"scalar": {"video_format": (0, 1, "u8"), "video_model": (1, 1, "u8"), "audio_gain": (2, 1, "u8"),
        "alarm_pre_record": (3, 4, "u32"), "alarm_delay": (7, 4, "u32"), "alarm_file_protect": (11, 1, "u8"),
        "display_resolution": (12, 1, "u8"), "osd_overlay": (13, 4, "u32")}},
    0xF026: {"slot": {"base": 0, "stride": 2, "count": 4, "fields": {"video_use": (0, 1, "u8"), "priority": (1, 1, "u8")}}},
    0xF100: {"scalar": {"enable": (0, 1, "u8"), "alarm_video": (1, 1, "u8"), "debug_mode": (2, 1, "u8"),
        "snap_enable": (3, 1, "u8"), "channel": (4, 1, "u8"), "delay": (5, 4, "u32"), "duration": (9, 4, "u32"),
        "l1_speed": (13, 1, "u8"), "l2_speed": (14, 1, "u8"), "fatigue": (15, 1, "u8"), "fatigue_threshold": (16, 4, "u32"),
        "fatigue_interval": (20, 2, "u16"), "yawn": (22, 1, "u8"), "eyes_closed": (23, 1, "u8"), "smoke": (24, 1, "u8"),
        "smoke_threshold": (25, 4, "u32"), "smoke_interval": (29, 2, "u16"), "phone": (31, 1, "u8"),
        "call_threshold": (32, 4, "u32"), "call_interval": (36, 2, "u16"), "distraction": (38, 1, "u8"),
        "distraction_threshold": (39, 4, "u32"), "distraction_interval": (43, 2, "u16"), "look_left": (45, 1, "u8"),
        "look_right": (46, 1, "u8"), "head_up": (47, 1, "u8"), "head_down": (48, 1, "u8"), "driver_abnormal": (49, 1, "u8"),
        "abnormal_threshold": (50, 4, "u32"), "abnormal_interval": (54, 2, "u16"), "no_face": (56, 1, "u8"),
        "off_seat": (57, 1, "u8"), "sunglasses": (58, 1, "u8"), "mouth_occlusion": (59, 1, "u8"), "shield_interval": (60, 2, "u16")}},
    0xF101: {"scalar": {"enable": (0, 1, "u8"), "alarm_video": (1, 1, "u8"), "channel": (2, 1, "u8"), "snap_enable": (3, 1, "u8"),
        "delay": (4, 4, "u32"), "duration": (8, 4, "u32"), "report_interval": (12, 2, "u16"), "l1_speed": (14, 1, "u8"),
        "l2_speed": (15, 1, "u8"), "left_ldw": (16, 1, "u8"), "right_ldw": (17, 1, "u8"), "ldw_threshold": (18, 4, "u32"),
        "fcw": (22, 1, "u8"), "fcw_threshold": (23, 4, "u32"), "pcw": (27, 1, "u8"), "pcw_threshold": (28, 4, "u32"),
        "hmw": (32, 1, "u8"), "hmw_threshold": (33, 4, "u32")}},
    0xF102: {"scalar": {"enable": (0, 1, "u8"), "alarm_video": (1, 1, "u8"), "curb_detection": (2, 1, "u8"),
        "channel": (3, 1, "u8"), "snap_enable": (4, 1, "u8"), "delay": (5, 4, "u32"), "duration": (9, 4, "u32"),
        "preview_switch": (13, 1, "u8"), "blind_area_attr": (14, 1, "u8")}},
    0xF105: {"scalar": {"enable": (0, 1, "u8"), "channel": (1, 1, "u8"), "accuracy": (2, 4, "u32"),
        "inhibition_time": (6, 4, "u32"), "cycle": (10, 4, "u32")}},
    0xF104: {"scalar": {"enable": (0, 1, "u8"), "alarm_video": (1, 1, "u8"), "channel": (2, 1, "u8"), "snap_enable": (3, 1, "u8"),
        "delay": (4, 1, "u8"), "duration": (5, 1, "u8"), "alarm_speed": (6, 1, "u8"), "seatbelt": (7, 1, "u8"),
        "phone_play": (8, 1, "u8"), "wheel_off": (9, 1, "u8"), "unbelt_threshold": (10, 4, "u32"),
        "phone_threshold": (14, 4, "u32"), "wheel_off_threshold": (18, 4, "u32"), "unbelt_interval": (22, 2, "u16"),
        "phone_interval": (24, 2, "u16"), "wheel_off_interval": (26, 2, "u16")}},
    # Record encoding (F024): 2 scalar + 16-byte channel-enable + maxChannel x 69-byte sub blocks
    0xF024: {"scalar": {"max_channel": (0, 1, "u8"), "audio_format": (1, 1, "u8")},
             "slot": {"base": 18, "stride": 69, "count": (lambda b: b[0] if b else 0), "fields": {
        "channel_type": (0, 1, "u8"), "encoding_format": (1, 1, "u8"), "channel": (2, 1, "u8"),
        "main_resolution": (3, 1, "u8"), "main_frame_rate": (4, 1, "u8"), "main_quality": (5, 1, "u8"),
        "main_record": (6, 1, "u8"), "main_bitrate_type": (7, 1, "u8"), "sub_resolution": (8, 1, "u8"),
        "sub_frame_rate": (9, 1, "u8"), "sub_quality": (10, 1, "u8"), "sub_record": (11, 1, "u8"),
        "sub_bitrate_type": (12, 1, "u8"), "port": (13, 4, "u32"), "ip": (17, 20, "str"),
        "user": (37, 16, "str"), "password": (53, 16, "str")}}},
    # Timed recording (F025): 8 slots x 12 bytes, each = 2 record windows (BCD hhmmss)
    0xF025: {"slot": {"base": 0, "stride": 12, "count": 8, "fields": {
        "start_time1": (0, 3, "bcd"), "end_time1": (3, 3, "bcd"),
        "start_time2": (6, 3, "bcd"), "end_time2": (9, 3, "bcd")}}},
    # Serial port SETTINGS (F018 — the safe query on this device; F017 = names, crashes it):
    # count(1) + count x 8-byte sub {enable, dataBit, stopBit, checkDigit, baudRate(u32)}
    0xF018: {"scalar": {"serial_port_num": (0, 1, "u8")},
             "slot": {"base": 1, "stride": 8, "count": (lambda b: b[0] if b else 0), "fields": {
        "enable": (0, 1, "u8"), "data_bit": (1, 1, "u8"), "stop_bit": (2, 1, "u8"),
        "check_digit": (3, 1, "u8"), "baud_rate": (4, 4, "u32")}}},
}
FIELD_META[0xF103] = FIELD_META[0xF102]   # BSD2 shares BSD's layout


def _off(fo, blen):
    return fo(blen) if callable(fo) else fo


def _dec_one(block, off, w, kind):
    if kind == "u8":  return block[off]
    if kind == "u16": return _u16(block, off)
    if kind == "u32": return _u32(block, off)
    if kind == "bcd": return _bcd2str(block[off:off + w])
    return _str(block, off, w)                       # str


def _enc_one(kind, w, value):
    if kind in ("u8", "u16", "u32"):
        n = int(value) & ((1 << (8 * w)) - 1)
        return n.to_bytes(w, "big")
    if kind == "bcd":
        s = "".join(c for c in str(value) if c.isdigit()).rjust(w * 2, "0")[:w * 2]
        return bytes(int(s[i:i + 2], 16) if False else (int(s[i]) << 4) | int(s[i + 1]) for i in range(0, w * 2, 2))
    return _rpad(str(value), w)                      # str (GB2312, null-padded)


def _slot_stride(sl, block):
    """Bytes-per-slot. May be a callable(block) so it can adapt to the ACTUAL packet size —
    older/newer firmware ship the same field COUNT but different per-slot byte sizes (e.g. IO
    grew 9->12 when evidence fields were added). Deriving stride from the received block keeps
    reads AND writes aligned on any firmware instead of hard-coding one generation's number."""
    s = sl["stride"]
    return s(block) if callable(s) else s


def read_fields(param: int, block: bytes) -> dict:
    """Decode every field of a section block into a FLAT {key: value} map keyed exactly like the
    UI's data-nat attributes: '<param>.<field>' (scalar) or '<param>.<slot>.<field>' (1-based)."""
    meta = FIELD_META.get(param)
    if not meta:
        return {}
    out, n = {}, len(block)
    for k, (fo, w, kind) in meta.get("scalar", {}).items():
        o = _off(fo, n)
        if o + w <= n:
            out[f"{param}.{k}"] = _dec_one(block, o, w, kind)
    sl = meta.get("slot")
    if sl:
        cnt = sl["count"](block) if callable(sl["count"]) else sl["count"]
        stride = _slot_stride(sl, block)
        for i in range(1, cnt + 1):
            base = sl["base"] + (i - 1) * stride
            for k, (fo, w, kind) in sl["fields"].items():
                if isinstance(fo, int) and fo + w > stride:   # field lives past this firmware's slot -> not present
                    continue
                o = base + _off(fo, n)
                if o + w <= n:
                    out[f"{param}.{i}.{k}"] = _dec_one(block, o, w, kind)
    return out


def patch_block(param: int, block: bytearray, subkey: str, value) -> bool:
    """Read-modify-write: write one field's value into `block` in place. subkey is '<field>'
    (scalar) or '<slot>.<field>'. Returns True if the field was known and written."""
    meta = FIELD_META.get(param)
    if not meta:
        return False
    n = len(block)
    parts = subkey.split(".")
    if len(parts) == 1:
        spec = meta.get("scalar", {}).get(parts[0])
        if not spec:
            return False
        fo, w, kind = spec
        base = 0
    else:
        sl = meta.get("slot")
        if not sl:
            return False
        slot = int(parts[0])
        spec = sl["fields"].get(parts[1])
        if not spec:
            return False
        fo, w, kind = spec
        stride = _slot_stride(sl, block)
        if isinstance(fo, int) and fo + w > stride:               # field not present on this firmware's slot
            return False                                          # -> write guard aborts the section
        base = sl["base"] + (slot - 1) * stride                   # same adaptive stride as read_fields
    o = base + _off(fo, n)
    if o + w > n:
        return False
    block[o:o + w] = _enc_one(kind, w, value)
    return True


# ── byte helpers (mirror com.vehicle.streaminglib.utils.BigBitOperator) ───────
def _u16(b, o):  return ((b[o] & 0xFF) << 8) | (b[o + 1] & 0xFF)               # twoBytes2Int (unsigned BE)
def _u32(b, o):  return ((b[o] & 0xFF) << 24) | ((b[o + 1] & 0xFF) << 16) | ((b[o + 2] & 0xFF) << 8) | (b[o + 3] & 0xFF)
def _s16(b, o):                                                                # convertTwoBytesToInt1 (signed BE)
    v = ((b[o] & 0xFF) << 8) | (b[o + 1] & 0xFF)
    return v - 0x10000 if v >= 0x8000 else v
def _str(b, o, n):                                                             # BitOperator.bytesToString (GB2312, null-trim)
    return b[o:o + n].split(b"\x00", 1)[0].decode("gb2312", "ignore").strip()


# ── 0x8C08 status block — EXACT transcription of GSensorResMessage.decode() ──
def parse_status_8c08(body: bytes) -> dict:
    """Decode the pushed 0x8C08 status block. Offsets are the app's own (GSensorResMessage).
    Returns whatever fixed-region fields fit in the body (the trailer fields are optional)."""
    b = body
    n = len(b)
    if n < 245:
        return {}
    o = {
        "license":          _str(b, 0, 16),        # vehicle plate / license number
        "driver":           _str(b, 16, 32),       # pilot / driver name
        "location_status":  b[48],                 # location status byte
        "speed":            _u16(b, 49) / 10.0,     # km/h
        "total_mileage":    _u32(b, 51) / 100.0,    # accumulated mileage
        "pulses":           _u32(b, 55),
        "io_status":        list(b[59:75]),         # 16 IO channels
        "acc_on":           b[75] != 0,             # ignition
        "hard_drive_lock":  b[76],
        "voltage":          _u16(b, 77) / 100.0,    # system voltage V
        "firmware":         _str(b, 79, 64),        # device version number
        "local_ip":         _str(b, 143, 16),
        "net_type":         b[159],
        "model_4g":         b[160],
        "net_type_4g":      b[161],
        "sim_signal":       b[162],
        "sim_status":       b[163],
        "dialing_status":   b[164],
        "dialing_ip":       _str(b, 165, 16),
        "wifi_model":       b[181],
        "wifi_ssid":        _str(b, 182, 24),
        "wifi_signal":      b[206],
        "wifi_ip":          _str(b, 207, 16),
        "accel_x":          _s16(b, 223),
        "accel_y":          _s16(b, 225),
        "accel_z":          _s16(b, 227),
    }
    # disks 1..4: total/used (u16 GB) at 229..244
    o["disks"] = [{"total": _u16(b, 229 + i * 4), "used": _u16(b, 231 + i * 4)} for i in range(4)]

    # optional trailer (present on full frames)
    if n >= 245 + 64:
        o["connected_platform"] = _str(b, 245, 64)
        if n >= 309 + 53:
            o["satellites"]       = _u32(b, 309)     # locationStar = satellites in view
            o["recording_status"] = b[313]
            o["iccid"]            = _str(b, 314, 24)
            o["imei"]             = _str(b, 338, 24)
            if n >= 366 + 1:
                o["temperature"]  = _u32(b, 362) / 100.0
                sp = b[366]
                o["serial_ports"] = sp
                # phone == the device/equipment number (app "Equipment No"), after the serial-port list
                poff = 367 + sp * 37
                if n >= poff + 20:
                    o["device_no"] = _str(b, poff, 20)
    return o


# ── shape the decode into the browser's device/telemetry message model ───────
def status_to_messages(st: dict):
    """Map a parse_status_8c08() dict onto the {device,...}/{telemetry,...} shapes the dashboard
    HTML already knows how to render. Returns (imei, [device_msgs...], telemetry_dict).
    imei = the vendor device number when present, else the license/IMEI as a fallback key."""
    imei = st.get("device_no") or st.get("imei") or st.get("license") or "device"

    identity = {"protocol": "JT/T 808"}
    if st.get("device_no"):
        identity["terminal_id"] = st["device_no"]
    if st.get("license"):
        identity["plate"] = st["license"]

    attributes = {}
    if st.get("iccid"):
        attributes["iccid"] = st["iccid"]

    auth = {}
    if st.get("firmware"):
        auth["software_version"] = st["firmware"]

    device_msgs = [
        {"type": "device", "imei": imei, "kind": "identity",   "data": identity},
        {"type": "device", "imei": imei, "kind": "attributes", "data": attributes},
        {"type": "device", "imei": imei, "kind": "auth",       "data": auth},
    ]

    tel = {"ts": None}   # ts stamped by caller (block carries no wall-clock)
    for src, dst in (("speed", "speed"), ("acc_on", "acc_on"), ("voltage", "voltage"),
                     ("total_mileage", "mileage"), ("satellites", "satellites"),
                     ("sim_signal", "network_signal"), ("wifi_signal", "wifi_signal"),
                     ("temperature", "temperature"), ("location_status", "location_status")):
        if src in st:
            tel[dst] = st[src]
    if "io_status" in st:
        tel["io_status"] = st["io_status"]
    # accelerometer
    if "accel_x" in st:
        tel["accel"] = {"x": st["accel_x"], "y": st["accel_y"], "z": st["accel_z"]}
    # no lat/lon here -> position stays unknown until RSP_LOCATION (0x0201) is wired
    tel["positioned"] = False
    return imei, device_msgs, tel
