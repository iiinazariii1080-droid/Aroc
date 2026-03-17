/**
 * ╔═══════════════════════════════════════════════════════════════╗
 * ║  CANONICAL FILE — DO NOT REPLACE / REGENERATE / AUTO-EDIT   ║
 * ║  This is the single source of truth for depth click overlay. ║
 * ║  Broken versions keep appearing during deploys — if you      ║
 * ║  need to change this, verify coordinate mapping first.       ║
 * ║  See: /memories/repo/DO_NOT_TOUCH_depth_features_js.md       ║
 * ╚═══════════════════════════════════════════════════════════════╝
 *
 * depth_features.js — Depth-click overlay for the depth-camera player.
 *
 * Injected into the depth_view.html page.  Adds:
 *   • crosshair cursor that follows the mouse over the <video>
 *   • click → HTTP GET /depth?x=…&y=… → depth value shown in HUD + badge
 *
 * All depth queries go through the local janus-camera-page `/depth` endpoint
 * which proxies to realsense_mux on port 8000.
 *
 * Coordinates sent to the server are in 0..100 range (percent of the
 * already-rotated video frame).  The CSS applies an additional 180° rotation,
 * so we compensate: frame_x = 100 - viewport_x, frame_y = 100 - viewport_y.
 *
 * The depth comes back in metres (or zero when unavailable).
 */
(function () {
  'use strict';

  // ── Config ──────────────────────────────────────────────────
  const DEPTH_API = (document.body.dataset.depthEndpoint ||
                     (document.body.dataset.apiPrefix || '') + '/depth');

  // ── State ───────────────────────────────────────────────────
  let video = null;

  // ── DOM references (from depth_view.html) ───────────────────
  let depthHud = null;   // #depthHud  — persistent x/y/depth readout
  let depthToast = null; // #depthToast — fade-in toast on click

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

      /* Cursor on video */
      video { cursor: crosshair !important; }
    `;
    document.head.appendChild(style);
  }

  function injectDOM() {
    const ch = document.createElement('div');
    ch.id = 'df-crosshair';
    document.body.appendChild(ch);

    const badge = document.createElement('div');
    badge.id = 'df-badge';
    document.body.appendChild(badge);
  }

  // ── Coordinate transform ────────────────────────────────────
  //
  //  Video pipeline:
  //    RealSense 640×480 → CW 90° in realsense_mux → 480×640 stream
  //    → WebRTC → browser <video> with object-fit:fill + CSS rotate(180°)
  //
  //  Depth frame in realsense_mux is rotated the same way as the video
  //  stream (CW 90°), so they share a coordinate system.  The only
  //  remaining transform is the CSS 180° rotation.
  //
  //  viewport click (x%, y%)  →  frame coords (100−x%, 100−y%)

  function viewportToFrame(xPct, yPct) {
    return { x: 100 - xPct, y: 100 - yPct };
  }

  const _DF_VERSION = 'v2-180deg';

  // ── Depth HTTP query ────────────────────────────────────────

  async function fetchDepth(frameX, frameY) {
    try {
      const url = `${DEPTH_API}?x=${frameX.toFixed(2)}&y=${frameY.toFixed(2)}`;
      const r = await fetch(url, { cache: 'no-store' });
      if (!r.ok) return null;
      const data = await r.json();
      return data.depth; // metres
    } catch (e) {
      console.warn('[depth_features] fetch error', e);
      return null;
    }
  }

  function formatDepth(d) {
    if (d == null || d === 0) return '--';
    if (d > 100) return (d / 1000).toFixed(3) + ' m';
    return d.toFixed(3) + ' m';
  }

  // ── HUD update ──────────────────────────────────────────────

  function updateHud(frameX, frameY, depth) {
    if (depthHud) {
      depthHud.innerHTML =
        `x: ${frameX.toFixed(1)}%  y: ${frameY.toFixed(1)}%<br>depth: ${formatDepth(depth)}`;
    }
  }

  function showToast(frameX, frameY, depth) {
    if (!depthToast) return;
    depthToast.innerHTML =
      `<strong>${formatDepth(depth)}</strong> @ (${frameX.toFixed(1)}%, ${frameY.toFixed(1)}%)`;
    depthToast.style.display = 'block';
    depthToast.style.opacity = '1';
    clearTimeout(depthToast._timer);
    depthToast._timer = setTimeout(() => {
      depthToast.style.opacity = '0';
      setTimeout(() => { depthToast.style.display = 'none'; }, 250);
    }, 3000);
  }

  // ── Click-to-depth ──────────────────────────────────────────

  function onVideoClick(e) {
    const rect = video.getBoundingClientRect();
    const vpX = ((e.clientX - rect.left) / rect.width) * 100;
    const vpY = ((e.clientY - rect.top) / rect.height) * 100;

    const frame = viewportToFrame(vpX, vpY);

    // Show crosshair at click position
    const ch = document.getElementById('df-crosshair');
    ch.style.left = e.clientX + 'px';
    ch.style.top = e.clientY + 'px';
    ch.style.display = 'block';

    // Show badge
    const badge = document.getElementById('df-badge');
    badge.style.left = e.clientX + 'px';
    badge.style.top = e.clientY + 'px';
    badge.textContent = '…';
    badge.style.display = 'block';

    console.log(`[depth_features ${_DF_VERSION}] click viewport=(${vpX.toFixed(1)}, ${vpY.toFixed(1)}) → frame=(${frame.x.toFixed(1)}, ${frame.y.toFixed(1)})`);

    fetchDepth(frame.x, frame.y).then(d => {
      console.log(`[depth_features ${_DF_VERSION}] depth=${d} at frame(${frame.x.toFixed(1)}, ${frame.y.toFixed(1)})`);
      badge.textContent = formatDepth(d);
      updateHud(frame.x, frame.y, d);
      showToast(frame.x, frame.y, d);
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

  // ── Init ────────────────────────────────────────────────────

  function init() {
    video = document.getElementById('video');
    if (!video) {
      console.warn('[depth_features] no <video id="video"> found');
      return;
    }

    const ds = document.body.dataset;
    const hudSel = ds.depthHud || '#depthHud';
    const toastSel = ds.depthToast || '#depthToast';
    depthHud = document.querySelector(hudSel);
    depthToast = document.querySelector(toastSel);

    injectCSS();
    injectDOM();

    video.addEventListener('click', onVideoClick);
    video.addEventListener('mousemove', onVideoMouseMove);
    video.addEventListener('mouseleave', onVideoMouseLeave);

    console.log(`[depth_features] initialised ${_DF_VERSION}, DEPTH_API =`, DEPTH_API);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
