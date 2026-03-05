/**
 * depth_features.js — Depth-click overlay for the depth-camera player.
 *
 * Injected into the depth_view.html page.  Adds:
 *   • crosshair cursor that follows the mouse over the <video>
 *   • click → HTTP GET /depth?x=…&y=… → depth value shown as overlay badge
 *   • 4-point continuous depth measurements (top / bottom / left / right)
 *     with gradient lines showing surface tilt
 *
 * All depth queries go through the local janus-camera-page `/depth` endpoint
 * which proxies to realsense_mux on port 8000.
 *
 * Coordinates sent to the server are in 0..100 range (percent of video frame).
 * The depth comes back in millimetres (or zero when unavailable).
 */
(function () {
  'use strict';

  // ── Config ──────────────────────────────────────────────────
  const DEPTH_API = (document.body.dataset.apiPrefix || '') + '/depth';
  const MEASURE_INTERVAL_MS = 2000;
  const COORD_SWAP = true; // server expects {x: 100-y, y: x} (rotated sensor)

  // ── State ───────────────────────────────────────────────────
  let video = null;
  let measureTimer = null;
  let depthConnected = false;

  // ── DOM injection ───────────────────────────────────────────

  function injectCSS() {
    const style = document.createElement('style');
    style.textContent = `
      /* Crosshair */
      #df-crosshair {
        position: fixed;
        width: 32px; height: 32px;
        pointer-events: none;
        z-index: 9000;
        display: none;
        transform: translate(-50%, -50%);
      }
      #df-crosshair::before, #df-crosshair::after {
        content: '';
        position: absolute;
        background: rgba(0, 255, 0, .8);
      }
      #df-crosshair::before { width: 2px; height: 100%; left: 50%; top: 0; transform: translateX(-50%); }
      #df-crosshair::after  { height: 2px; width: 100%; top: 50%; left: 0; transform: translateY(-50%); }

      /* Depth badge (shown on click) */
      #df-badge {
        position: fixed;
        z-index: 9001;
        pointer-events: none;
        display: none;
        background: rgba(0,0,0,.75);
        color: #0f0;
        font: bold 16px/1.2 monospace;
        padding: 4px 10px;
        border-radius: 6px;
        border: 1px solid rgba(0,255,0,.4);
        text-shadow: 0 0 4px rgba(0,255,0,.6);
        transform: translate(16px, -50%);
        white-space: nowrap;
      }

      /* 4-point depth labels */
      .df-point-label {
        position: absolute;
        z-index: 8999;
        pointer-events: none;
        color: #fff;
        font: bold 11px/1 monospace;
        text-shadow: 1px 1px 2px #000;
        background: rgba(0,0,0,.6);
        padding: 2px 5px;
        border-radius: 3px;
        min-width: 36px;
        text-align: center;
      }
      .df-point-dot {
        position: absolute;
        z-index: 8998;
        pointer-events: none;
        width: 8px; height: 8px;
        border-radius: 50%;
        background: #f00;
        box-shadow: 0 0 6px rgba(255,0,0,.9);
        border: 1px solid rgba(255,255,255,.5);
        transform: translate(-50%, -50%);
      }

      /* Gradient tilt lines */
      #df-grad-v, #df-grad-h {
        position: absolute;
        z-index: 8997;
        pointer-events: none;
        border-radius: 2px;
        box-shadow: 0 0 5px rgba(0,255,0,.5);
      }
      #df-grad-v {
        top: 8px; right: 8px;
        width: 4px; height: 120px;
        background: linear-gradient(to bottom, #0f0, #0f0);
      }
      #df-grad-h {
        bottom: 8px; right: 8px;
        width: 120px; height: 4px;
        background: linear-gradient(to right, #0f0, #0f0);
      }

      /* Cursor on video */
      video { cursor: crosshair !important; }
    `;
    document.head.appendChild(style);
  }

  function injectDOM() {
    // Crosshair
    const ch = document.createElement('div');
    ch.id = 'df-crosshair';
    document.body.appendChild(ch);

    // Badge
    const badge = document.createElement('div');
    badge.id = 'df-badge';
    document.body.appendChild(badge);

    // 4-point measurement elements — relative to body (absolute positioning)
    const points = [
      { id: 'top',    dotStyle: 'top:12%;left:50%',   labelStyle: 'top:calc(12% + 12px);left:50%;transform:translateX(-50%)' },
      { id: 'bottom', dotStyle: 'bottom:12%;left:50%', labelStyle: 'bottom:calc(12% + 12px);left:50%;transform:translateX(-50%)' },
      { id: 'left',   dotStyle: 'top:50%;left:12%',    labelStyle: 'top:50%;left:calc(12% + 12px);transform:translateY(-50%)' },
      { id: 'right',  dotStyle: 'top:50%;right:12%',   labelStyle: 'top:50%;right:calc(12% + 12px);transform:translateY(-50%)' },
    ];
    points.forEach(p => {
      const dot = document.createElement('div');
      dot.className = 'df-point-dot';
      dot.style.cssText = p.dotStyle;
      document.body.appendChild(dot);

      const lbl = document.createElement('div');
      lbl.className = 'df-point-label';
      lbl.id = `df-lbl-${p.id}`;
      lbl.style.cssText = p.labelStyle;
      lbl.textContent = '--';
      document.body.appendChild(lbl);
    });

    // Gradient lines
    const gv = document.createElement('div'); gv.id = 'df-grad-v'; document.body.appendChild(gv);
    const gh = document.createElement('div'); gh.id = 'df-grad-h'; document.body.appendChild(gh);
  }

  // ── Depth HTTP query ────────────────────────────────────────

  /**
   * Query depth at (xPct, yPct) where 0..100 = percent of video frame.
   * Returns depth in metres, or null.
   */
  async function fetchDepth(xPct, yPct) {
    // The server's coordinate mapping: the D435 is rotated CW 90°.
    // Server expects message JSON {x, y} where x = 100 - clientY%, y = clientX%.
    const msg = COORD_SWAP
      ? JSON.stringify({ x: (100 - yPct).toFixed(1), y: xPct.toFixed(1) })
      : JSON.stringify({ x: xPct.toFixed(1), y: yPct.toFixed(1) });

    try {
      const r = await fetch(`${DEPTH_API}?message=${encodeURIComponent(msg)}`, { cache: 'no-store' });
      if (!r.ok) return null;
      const data = await r.json();
      return data.depth; // millimetres (or metres depending on pipeline)
    } catch (e) {
      console.warn('[depth_features] fetch error', e);
      return null;
    }
  }

  function formatDepth(d) {
    if (d == null || d === 0) return '--';
    // If > 100 assume mm, else assume metres
    if (d > 100) return (d / 1000).toFixed(3) + ' m';
    return d.toFixed(3) + ' m';
  }

  // ── Click-to-depth ──────────────────────────────────────────

  function onVideoClick(e) {
    const rect = video.getBoundingClientRect();
    const xPct = ((e.clientX - rect.left) / rect.width) * 100;
    const yPct = ((e.clientY - rect.top) / rect.height) * 100;

    // Show crosshair at click
    const ch = document.getElementById('df-crosshair');
    ch.style.left = e.clientX + 'px';
    ch.style.top = e.clientY + 'px';
    ch.style.display = 'block';

    // Fetch depth
    const badge = document.getElementById('df-badge');
    badge.style.left = e.clientX + 'px';
    badge.style.top = e.clientY + 'px';
    badge.textContent = '…';
    badge.style.display = 'block';

    fetchDepth(xPct, yPct).then(d => {
      badge.textContent = formatDepth(d);
      depthConnected = (d != null);
    });
  }

  function onVideoMouseMove(e) {
    const ch = document.getElementById('df-crosshair');
    ch.style.left = e.clientX + 'px';
    ch.style.top = e.clientY + 'px';
    ch.style.display = 'block';
  }

  function onVideoMouseLeave() {
    document.getElementById('df-crosshair').style.display = 'none';
  }

  // ── 4-point continuous measurement ──────────────────────────

  const MEASURE_POINTS = [
    { id: 'top',    x: 50, y: 20 },
    { id: 'bottom', x: 50, y: 80 },
    { id: 'left',   x: 20, y: 50 },
    { id: 'right',  x: 80, y: 50 },
  ];

  async function measureAllPoints() {
    const results = {};
    for (const pt of MEASURE_POINTS) {
      const d = await fetchDepth(pt.x, pt.y);
      const lbl = document.getElementById(`df-lbl-${pt.id}`);
      if (lbl) lbl.textContent = formatDepth(d);
      results[pt.id] = d;
    }
    updateGradients(results);
  }

  function updateGradients(r) {
    const topD = parseFloat(document.getElementById('df-lbl-top')?.textContent) || 0;
    const botD = parseFloat(document.getElementById('df-lbl-bottom')?.textContent) || 0;
    const lftD = parseFloat(document.getElementById('df-lbl-left')?.textContent) || 0;
    const rgtD = parseFloat(document.getElementById('df-lbl-right')?.textContent) || 0;

    updateGradientLine('df-grad-v', Math.abs(topD - botD), true);
    updateGradientLine('df-grad-h', Math.abs(lftD - rgtD), false);
  }

  function updateGradientLine(id, diff, vertical) {
    const el = document.getElementById(id);
    if (!el) return;
    const n = Math.min(diff / 2.0, 1.0); // normalise to 0..1 over 2m range
    const r = n, g = 1 - n;
    if (vertical) {
      el.style.background = `linear-gradient(to bottom, rgba(255,0,0,${r}), rgba(0,255,0,${g}))`;
    } else {
      el.style.background = `linear-gradient(to right, rgba(255,0,0,${r}), rgba(0,255,0,${g}))`;
    }
  }

  // ── Init ────────────────────────────────────────────────────

  function init() {
    video = document.getElementById('video');
    if (!video) {
      console.warn('[depth_features] no <video id="video"> found');
      return;
    }

    injectCSS();
    injectDOM();

    video.addEventListener('click', onVideoClick);
    video.addEventListener('mousemove', onVideoMouseMove);
    video.addEventListener('mouseleave', onVideoMouseLeave);

    // Start continuous 4-point measurements
    measureTimer = setInterval(measureAllPoints, MEASURE_INTERVAL_MS);
    // First measurement right away
    setTimeout(measureAllPoints, 1000);

    console.log('[depth_features] initialised, DEPTH_API =', DEPTH_API);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
