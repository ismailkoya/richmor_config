/* richmor_codec_jt808.js — browser-side JT/T 808 codec.
 * The SINGLE source of UI-side JT808 protocol logic: command/ack translation + hex builders.
 * Mirror of richmor_codec_jt808.py (describe_command / describe_ack / CMD_PARAMS). NO JT808 hex is
 * parsed or built anywhere else in the dashboard — everything routes through window.RJT808.
 * Loaded via <script src> BEFORE the dashboard's inline script. */
(function (g) {
  const PARAMS = {
    '00000001': ['HEARTBEAT', 's'], '00000010': ['APN', 'str'], '00000013': ['SERVER', 'str'],
    '00000018': ['TCP PORT', ''], '00000019': ['UDP PORT', ''], '00000029': ['REPORT INTERVAL', 's'],
    '0000002C': ['REPORT DISTANCE', 'm'], '00000055': ['OVERSPEED', 'km/h'], '00000056': ['OVERSPEED DURATION', 's'],
    '00000057': ['DRIVING LIMIT', 's'], '00000080': ['ODOMETER', '']
  };
  const CTRL = { 1: 'OTA upgrade', 3: 'Shutdown', 4: 'Reboot', 5: 'Factory reset', 6: 'Close data comm', 7: 'Close all wireless' };
  const RESULT = { 0: 'OK', 1: 'FAILED', 2: 'WRONG MESSAGE', 3: 'UNSUPPORTED' };
  // id -> human description (single source of truth for param help text; mirrored in richmor_codec_jt808.py)
  const PARAM_DESC = {
    '00000001': 'Interval between terminal heartbeat messages.',
    '00000002': 'How long to wait for a TCP message acknowledgement.',
    '00000003': 'Times a TCP message is retransmitted before giving up.',
    '00000004': 'How long to wait for a UDP message acknowledgement.',
    '00000005': 'Times a UDP message is retransmitted before giving up.',
    '00000006': 'How long to wait for an SMS acknowledgement.',
    '00000007': 'Times an SMS is retransmitted before giving up.',
    '00000013': 'Main platform IP address or domain name.',
    '00000018': 'Main platform TCP port.',
    '00000019': 'Main platform UDP port.',
    '00000010': 'Access point name for the primary SIM card.',
    '00000011': 'Cellular dial-up username for the primary SIM card.',
    '00000012': 'Access point name password for the primary SIM card.',
    '00000014': 'APN for the backup server connection.',
    '00000015': 'Dial-up username for the backup server.',
    '00000016': 'Dial-up password for the backup server.',
    '00000017': 'Backup platform IP address or domain name.',
    '0000001A': 'Road-transport IC-card authentication main server.',
    '0000001B': 'TCP port of the IC-card authentication main server.',
    '0000001C': 'UDP port of the IC-card authentication main server.',
    '0000001D': 'IC-card authentication backup server address.',
    '00000020': 'When to report position: by time, by distance, or both.',
    '00000021': 'Whether reporting follows ACC state or login state.',
    '00000022': 'Position report interval when no driver is logged in.',
    '00000027': 'Position report interval while asleep (ACC off).',
    '00000028': 'Position report interval while an emergency alarm is active.',
    '00000029': 'Default position report interval, by time.',
    '0000002C': 'Default position report interval, by distance.',
    '0000002D': 'Distance report interval when no driver is logged in.',
    '0000002E': 'Distance report interval while asleep.',
    '0000002F': 'Distance report interval during an emergency.',
    '00000030': 'Heading change that triggers an extra corner report.',
    '00000031': 'Default radius for a circular geofence.',
    '00000055': 'Overspeed threshold.',
    '00000056': 'How long above the limit before an overspeed alarm fires.',
    '00000057': 'Maximum continuous driving time before a fatigue alarm.',
    '00000058': 'Maximum cumulative driving time in one day.',
    '00000059': 'Minimum rest time required after driving.',
    '0000005A': 'Maximum allowed continuous parking time.',
    '0000005B': 'Pre-warning margin below the overspeed limit.',
    '0000005C': 'Pre-warning margin before the fatigue-driving limit.',
    '0000005D': 'Collision-alarm sensitivity parameters.',
    '0000005E': 'Rollover-alarm sensitivity parameters.',
    '00000050': 'Bitmask of alarms to suppress (masked alarms are not sent).',
    '00000051': 'Bitmask of alarms that also send an SMS.',
    '00000052': 'Bitmask of alarms that trigger a snapshot.',
    '00000053': 'Bitmask of alarm snapshots stored vs uploaded.',
    '00000054': 'Bitmask of alarms marked as key (priority) events.',
    '00000064': 'Enabled constellations: GPS / BeiDou / GLONASS / Galileo (bitmask).',
    '00000065': 'GNSS module serial baud rate.',
    '00000093': 'On-board dense GNSS log cadence — the 5-second track.',
    '00000094': 'How the dense GNSS log is uploaded to the platform.',
    '00000095': 'Parameters for the dense GNSS log upload.',
    '00000070': 'CAN channel 1 data collection interval.',
    '00000071': 'CAN channel 1 data upload interval.',
    '00000072': 'CAN channel 2 data collection interval.',
    '00000073': 'CAN channel 2 data upload interval.',
    '00000074': 'Per-CAN-ID individual collection settings (bitmask).',
    '00000040': 'Phone number of the monitoring platform.',
    '00000041': 'Phone number allowed to reset the terminal.',
    '00000042': 'Phone number allowed to factory-reset the terminal.',
    '00000043': 'Monitoring platform SMS number.',
    '00000044': 'Number that receives terminal SMS text alarms.',
    '00000045': 'How the terminal answers incoming calls.',
    '00000046': 'Maximum duration of a single call.',
    '00000047': 'Maximum total call time per month.',
    '00000048': 'Number used for remote audio monitoring.',
    '00000049': 'Regulator privileged-SMS number.',
    '00000080': 'Vehicle odometer reading.',
    '00000081': 'Province code where the vehicle is registered.',
    '00000082': 'City/county code where the vehicle is registered.',
    '00000083': 'Registered vehicle licence-plate number.',
    '00000084': 'Licence-plate colour.'
  };
  function desc(id) { return PARAM_DESC[String(id).toUpperCase()] || ''; }

  function pName(id) { const p = PARAMS[id.toUpperCase()]; return p ? p[0] : ('PARAM 0x' + id.replace(/^0+/, '').toUpperCase()); }
  function pVal(id, valHex) {
    const p = PARAMS[id.toUpperCase()];
    if (p && p[1] === 'str') { let s = ''; for (let i = 0; i + 2 <= valHex.length; i += 2) s += String.fromCharCode(parseInt(valHex.substr(i, 2), 16)); return s; }
    const n = parseInt(valHex || '0', 16) || 0; return n + (p && p[1] ? (' ' + p[1]) : '');
  }
  // count(1) + [id(4) len(1) val] — RESYNC-tolerant: real devices wedge a non-standard vendor block
  // (e.g. 0x79xx) between the standard params, which desyncs a naive walk and loses every later param
  // (odometer/plate/GNSS/…). We accept a TLV only when the id is plausible (ascending, standard <=0x0200
  // or Subiao 0xF3xx) and its value fits; otherwise we skip one byte and re-hunt for the next real TLV.
  function parseParams(hex) {
    const out = []; let i = 2, last = -1, n = hex.length;      // i=2 skips the count byte
    while (i + 10 <= n) {
      const idn = parseInt(hex.substr(i, 8), 16), L = parseInt(hex.substr(i + 8, 2), 16);
      if (idn > last && (idn <= 0x0200 || (idn >= 0xF300 && idn <= 0xF3FF)) && i + 10 + L * 2 <= n) {
        out.push({ id: hex.substr(i, 8), val: hex.substr(i + 10, L * 2) }); last = idn; i += 10 + L * 2;
      } else { i += 2; }                                        // vendor/garbage -> resync one byte
    }
    return out;
  }
  function parseIds(hex) { const out = []; let i = 2; while (i + 8 <= hex.length) { out.push(hex.substr(i, 8)); i += 8; } return out; }

  /* dir 'out'|'in', mid like '0x8103', body hex. lastOut = last sent command's text (to phrase a bare 0x0001 ack).
   * Returns '' when this codec does not own the message (so the dashboard can try the TTX codec). */
  function translate(dir, mid, body, lastOut) {
    mid = (mid || '').toUpperCase(); body = (body || '').toUpperCase();
    if (dir === 'out') {
      if (mid === '0X8104') return 'Read all parameters';
      if (mid === '0X8106') { const ids = parseIds(body); return ids.length ? ('Read ' + ids.map(pName).join(', ')) : 'Read parameter'; }
      if (mid === '0X8103') { const ps = parseParams(body); return ps.length ? ('Write ' + ps.map(p => pName(p.id) + ' ' + pVal(p.id, p.val)).join('; ')) : 'Write parameters'; }
      if (mid === '0X8105') { const c = parseInt(body.substr(0, 2), 16); return CTRL[c] || ('Control cmd ' + c); }
      return '';
    }
    if (mid === '0X0001') { const r = parseInt(body.substr(8, 2), 16); const rn = RESULT[r] || ('result ' + r); return (lastOut ? lastOut + ' — ' : '') + rn; }
    if (mid === '0X0104') { const ps = parseParams(body.slice(4)); return ps.length ? (ps.map(p => pName(p.id) + ' ' + pVal(p.id, p.val)).join('; ') + ' — OK') : 'Parameters — OK'; }
    return '';
  }

  // ── outbound builders (return hex strings) ──
  function dword(n) { n = parseInt(String(n).replace(/[^0-9]/g, ''), 10); if (!isFinite(n) || n < 0) n = 0; return (n >>> 0).toString(16).toUpperCase().padStart(8, '0'); }
  function ascii(s) { return Array.from(String(s)).map(c => c.charCodeAt(0).toString(16).padStart(2, '0')).join('').toUpperCase(); }
  function param(idHex, valHex) { return idHex + (valHex.length / 2).toString(16).toUpperCase().padStart(2, '0') + valHex; }
  function setParams() { const p = Array.prototype.slice.call(arguments); return '8103' + p.length.toString(16).toUpperCase().padStart(2, '0') + p.join(''); }

  g.RJT808 = { PARAMS, PARAM_DESC, desc, pName, pVal, parseParams, parseIds, translate, dword, ascii, param, setParams };
})(window);
