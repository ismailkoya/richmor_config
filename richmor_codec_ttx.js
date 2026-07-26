/* richmor_codec_ttx.js — browser-side native TTX ($$dc) codec.
 * The SINGLE source of UI-side TTX protocol logic. Mirror of richmor_codec_ttx.py. Today the UI only
 * needs to label a raw $$ frame the operator typed; C-command translation grows here as the TTX command
 * path lands. NO TTX logic lives anywhere else in the dashboard — everything routes through window.RTTX.
 * Loaded via <script src> BEFORE the dashboard's inline script. */
(function (g) {
  /* Returns '' when this codec does not own the message (dashboard then falls back to the JT808 codec). */
  function translate(dir, mid, body, lastOut) {
    mid = (mid || '').toUpperCase();
    if (dir === 'out' && mid === '0X2424') return 'Raw frame ($$ channel)';
    return '';
  }

  g.RTTX = { translate };
})(window);
