/**
 * gripper_reticle.js — Dynamic gripper jaw projection overlay
 *
 * Projects the physical gripper jaw contact points onto the depth camera
 * image using a pinhole camera model. The reticle (two circles + connecting
 * line) scales dynamically based on measured depth.
 *
 * ── Coordinate systems ──
 *
 *  Camera frame (pinhole):  X → right,  Y → down,  Z → forward (depth)
 *  Sensor pixel:            u = fx·X/Z + cx,   v = fy·Y/Z + cy
 *  CSS viewport:            rotated 180° relative to sensor pixel space
 *
 * ── Configuration (data-* attributes on <body>) ──
 *
 *  data-gripper-fx           Focal length X (px)         default 380.43
 *  data-gripper-fy           Focal length Y (px)         default 380.43
 *  data-gripper-cx           Principal point X (px)      default 320
 *  data-gripper-cy           Principal point Y (px)      default 240
 *  data-gripper-img-w        Sensor width (px)           default 640
 *  data-gripper-img-h        Sensor height (px)          default 480
 *  data-gripper-offset-x     Camera→gripper X (mm)       default 30
 *  data-gripper-offset-y     Camera→gripper Y (mm)       default -52
 *  data-gripper-offset-z     Camera→gripper Z (mm)       default 60
 *  data-gripper-jaw-spacing  Jaw center-to-center (mm)   default 106
 *  data-gripper-jaw-diameter Circle diameter (mm)         default 30
 *  data-gripper-depth-scale  Depth correction factor     default 1.0
 *  data-gripper-poll-ms      Update interval (ms)        default 400
 *  data-depth-endpoint       Depth API URL               required
 */
(() => {
  'use strict';

  function parseNum(raw, fallback) {
    const n = Number(raw);
    return Number.isFinite(n) ? n : fallback;
  }

  function initGripperReticle() {
    const body = document.body;
    if (!body) return;
    const ds = body.dataset || {};

    // ── Camera intrinsics ──
    const fx   = parseNum(ds.gripperFx, 380.4253845214844);
    const fy   = parseNum(ds.gripperFy, 380.4253845214844);
    const cx   = parseNum(ds.gripperCx, 320);
    const cy   = parseNum(ds.gripperCy, 240);
    const imgW = parseNum(ds.gripperImgW, 640);
    const imgH = parseNum(ds.gripperImgH, 480);

    // ── Gripper offset in camera frame (mm) ──
    // Positive X = gripper center is to the RIGHT of optical center
    // Positive Y = gripper center is BELOW optical center (camera Y-down)
    // Positive Z = gripper is AHEAD of camera (into scene)
    const Gx = parseNum(ds.gripperOffsetX, 0);
    const Gy = parseNum(ds.gripperOffsetY, -52);
    const Gz = parseNum(ds.gripperOffsetZ, 60);

    // ── Gripper geometry ──
    const jawSpacing  = parseNum(ds.gripperJawSpacing, 106);   // mm center-to-center
    const jawDiameter = parseNum(ds.gripperJawDiameter, 30);   // mm
    const jawRadius   = jawDiameter / 2;

    // ── Depth config ──
    const depthScale = parseNum(ds.gripperDepthScale, 1.0);
    const pollMs     = parseNum(ds.gripperPollMs, 400);

    // ── Depth API endpoint ──
    const PROTOCOL = window.location.protocol;
    const HOST     = window.location.host;
    const rawEndpoint = ds.depthEndpoint || null;
    if (!rawEndpoint) {
      console.warn('[gripper_reticle] data-depth-endpoint not set, disabled');
      return;
    }
    const depthEndpoint = rawEndpoint.startsWith('/')
      ? `${PROTOCOL}//${HOST}${rawEndpoint}`
      : rawEndpoint;

    const videoEl = document.getElementById(ds.videoId || 'video');
    if (!videoEl) {
      console.warn('[gripper_reticle] video element not found');
      return;
    }

    // ── SVG overlay ──────────────────────────────────────────────────

    const NS = 'http://www.w3.org/2000/svg';

    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('id', 'gripperReticle');
    svg.style.cssText = [
      'position: fixed',
      'inset: 0',
      'width: 100vw',
      'height: 100vh',
      'z-index: 14',
      'pointer-events: none',
      'overflow: hidden',
    ].join('; ');

    // Connecting line between jaw centers
    const line = document.createElementNS(NS, 'line');
    line.setAttribute('stroke', 'rgba(255, 60, 50, 0.7)');
    line.setAttribute('stroke-width', '2');
    line.setAttribute('stroke-dasharray', '6 4');

    // Left jaw circle
    const circleL = document.createElementNS(NS, 'circle');
    circleL.setAttribute('fill', 'none');
    circleL.setAttribute('stroke', 'rgba(255, 60, 50, 0.85)');
    circleL.setAttribute('stroke-width', '2.5');

    // Right jaw circle
    const circleR = document.createElementNS(NS, 'circle');
    circleR.setAttribute('fill', 'none');
    circleR.setAttribute('stroke', 'rgba(255, 60, 50, 0.85)');
    circleR.setAttribute('stroke-width', '2.5');

    // Depth label
    const label = document.createElementNS(NS, 'text');
    label.setAttribute('fill', 'rgba(255, 60, 50, 0.9)');
    label.setAttribute('font-family', 'SF Mono, ui-monospace, Menlo, monospace');
    label.setAttribute('font-size', '13');
    label.setAttribute('text-anchor', 'middle');

    svg.appendChild(line);
    svg.appendChild(circleL);
    svg.appendChild(circleR);
    svg.appendChild(label);
    document.body.appendChild(svg);

    // ── Sensor pixel → screen coordinate mapping ─────────────────────
    //
    //  Pipeline: sensor 640×480 → CW 90° rotation (realsense_mux) → 480×640
    //            → CSS 180° rotation → final screen
    //
    //  CW 90°:  sensor (u, v) → rotated (imgH-1-v, u) in frame 480×640
    //  Fill:    stretch rotated frame to viewport
    //  CSS 180°: flip around viewport center

    const rotW = imgH;  // rotated frame width  = 480
    const rotH = imgW;  // rotated frame height = 640

    function sensorToScreen(u, v) {
      const vw = window.innerWidth;
      const vh = window.innerHeight;

      // Step 1: CW 90° frame rotation (np.rot90 k=3)
      const ru = (imgH - 1) - v;  // rotated x (0..imgH)
      const rv = u;               // rotated y (0..imgW)

      // Step 2: object-fit: fill — stretch to viewport
      let sx = (ru / rotW) * vw;
      let sy = (rv / rotH) * vh;

      // Step 3: 180° CSS rotation around viewport center
      sx = vw - sx;
      sy = vh - sy;

      return { x: sx, y: sy };
    }

    function sensorPxToScreenPx(sensorPx) {
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      // After CW 90° + fill, use average scale for circle radius
      const scaleX = vw / rotW;
      const scaleY = vh / rotH;
      const scale = (scaleX + scaleY) / 2;
      return Math.abs(sensorPx) * scale;
    }

    // ── Depth fetching ───────────────────────────────────────────────

    let depthInFlight = false;

    /**
     * Fetch depth at a point.  x,y are 0–100 in rotated-frame coords.
     */
    async function fetchDepthAt(xPct, yPct) {
      try {
        const url = `${depthEndpoint}?x=${xPct.toFixed(2)}&y=${yPct.toFixed(2)}`;
        const resp = await fetch(url, { cache: 'no-store' });
        if (!resp.ok) return null;
        const data = await resp.json();
        if (!Number.isFinite(data.depth) || data.depth <= 0) return null;
        return data.depth; // meters
      } catch (e) {
        return null;
      }
    }

    /**
     * Convert sensor pixel (u,v) → rotated-frame percentage (0–100).
     * CW 90°: (u,v) → rotated (imgH-1-v, u) in frame 480×640.
     */
    function sensorToRotatedPct(u, v) {
      const ru = (imgH - 1) - v;
      const rv = u;
      return {
        xPct: (ru / (rotW - 1)) * 100,
        yPct: (rv / (rotH - 1)) * 100,
      };
    }

    async function fetchJawDepths() {
      if (depthInFlight) return null;
      depthInFlight = true;
      try {
        // Center depth
        const dCenter = await fetchDepthAt(50, 50);
        if (dCenter === null) return null;

        // Project jaw centers at this depth to get their sensor positions
        const proj = projectJaws(dCenter);
        if (!proj) return { center: dCenter, left: null, right: null };

        // Convert jaw sensor pixels to rotated-frame percentages
        const pL = sensorToRotatedPct(proj.uL, proj.vL);
        const pR = sensorToRotatedPct(proj.uR, proj.vR);

        const [dL, dR] = await Promise.all([
          fetchDepthAt(pL.xPct, pL.yPct),
          fetchDepthAt(pR.xPct, pR.yPct),
        ]);

        return { center: dCenter, left: dL, right: dR };
      } finally {
        depthInFlight = false;
      }
    }

    // ── Pinhole projection ───────────────────────────────────────────
    //
    //  At surface depth D (mm from camera):
    //    Left jaw 3D:  (Gx − spacing/2,  Gy,  D)
    //    Right jaw 3D: (Gx + spacing/2,  Gy,  D)
    //
    //    u = fx · X / D + cx
    //    v = fy · Y / D + cy
    //    r_px = fx · jawRadius / D

    function projectJaws(depthMeters) {
      const D = depthMeters * 1000 * depthScale; // meters → mm, with correction
      if (D <= 10) return null; // too close / invalid

      const halfSpacing = jawSpacing / 2;

      // Jaws spread along X in sensor frame → vertical on screen after rotations
      const xL   = Gx - halfSpacing;
      const xR   = Gx + halfSpacing;
      const yJaw = Gy;

      const uL  = fx * xL / D + cx;
      const vL  = fy * yJaw / D + cy;
      const uR  = fx * xR / D + cx;
      const vR  = fy * yJaw / D + cy;
      const rPx = fx * jawRadius / D;

      return { uL, vL, uR, vR, rPx, D };
    }

    // ── Depth-difference color gradient ───────────────────────────────────────
    //
    //  |dL − dR|  ≤ 10mm  → green
    //             ≤ 25mm  → yellow
    //             ≤ 50mm  → orange
    //             > 50mm  → red

    function diffColor(dL, dR) {
      if (dL === null || dR === null) return 'rgba(255, 60, 50, 0.85)'; // unknown → default red
      const diffMm = Math.abs(dL - dR) * 1000;
      if (diffMm <= 10) return 'rgba(48, 209, 88, 0.9)';   // green
      if (diffMm <= 25) return 'rgba(255, 214, 10, 0.9)';  // yellow
      if (diffMm <= 50) return 'rgba(255, 159, 10, 0.9)';  // orange
      return 'rgba(255, 69, 58, 0.9)';                     // red
    }

    function diffLineColor(dL, dR) {
      if (dL === null || dR === null) return 'rgba(255, 60, 50, 0.7)';
      const diffMm = Math.abs(dL - dR) * 1000;
      if (diffMm <= 10) return 'rgba(48, 209, 88, 0.6)';
      if (diffMm <= 25) return 'rgba(255, 214, 10, 0.6)';
      if (diffMm <= 50) return 'rgba(255, 159, 10, 0.6)';
      return 'rgba(255, 69, 58, 0.6)';
    }

    // ── Render ────────────────────────────────────────────────────────────────

    let lastDepth = null;
    let lastJawDepths = { left: null, right: null };
    let visible = true;

    function hideOverlay() {
      svg.style.display = 'none';
      visible = false;
    }

    function showOverlay() {
      svg.style.display = '';
      visible = true;
    }

    function render(depthMeters, jawDepthL, jawDepthR) {
      const proj = projectJaws(depthMeters);
      if (!proj) { hideOverlay(); return; }

      const { uL, vL, uR, vR, rPx } = proj;
      const left    = sensorToScreen(uL, vL);
      const right   = sensorToScreen(uR, vR);
      const rScreen = sensorPxToScreenPx(rPx);

      // ── Force midpoint to exact viewport center ──
      const rawMidX = (left.x + right.x) / 2;
      const rawMidY = (left.y + right.y) / 2;
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      const dx = vw / 2 - rawMidX;
      const dy = vh / 2 - rawMidY;
      left.x  += dx;  left.y  += dy;
      right.x += dx;  right.y += dy;

      // Color by depth difference
      const clr     = diffColor(jawDepthL, jawDepthR);
      const lineClr = diffLineColor(jawDepthL, jawDepthR);

      circleL.setAttribute('cx', left.x.toFixed(1));
      circleL.setAttribute('cy', left.y.toFixed(1));
      circleL.setAttribute('r',  Math.max(2, rScreen).toFixed(1));
      circleL.setAttribute('stroke', clr);

      circleR.setAttribute('cx', right.x.toFixed(1));
      circleR.setAttribute('cy', right.y.toFixed(1));
      circleR.setAttribute('r',  Math.max(2, rScreen).toFixed(1));
      circleR.setAttribute('stroke', clr);

      line.setAttribute('x1', left.x.toFixed(1));
      line.setAttribute('y1', left.y.toFixed(1));
      line.setAttribute('x2', right.x.toFixed(1));
      line.setAttribute('y2', right.y.toFixed(1));
      line.setAttribute('stroke', lineClr);

      const midX = (left.x + right.x) / 2;
      const midY = (left.y + right.y) / 2 - rScreen - 8;
      label.setAttribute('x', midX.toFixed(1));
      label.setAttribute('y', midY.toFixed(1));
      label.setAttribute('fill', clr);
      label.textContent = `${depthMeters.toFixed(3)} m`;

      if (!visible) showOverlay();
    }

    // ── Poll loop (gated by player state) ────────────────────────────

    let timer = null;
    let isPlaying = false;

    async function tick() {
      if (document.hidden || !isPlaying) return;
      const result = await fetchJawDepths();
      if (result !== null) {
        lastDepth = result.center;
        lastJawDepths = { left: result.left, right: result.right };
        render(result.center, result.left, result.right);
      }
    }

    function start() {
      if (timer) return;
      tick();
      timer = setInterval(tick, pollMs);
    }

    function stop() {
      if (timer) { clearInterval(timer); timer = null; }
      hideOverlay();
      lastDepth = null;
    }

    // ── Observe player state via statusPill data-state attribute ──
    function onPlayerStateChange(state) {
      const playing = (state === 'PLAYING');
      if (playing === isPlaying) return;
      isPlaying = playing;
      if (playing) {
        console.log('[gripper_reticle] PLAYING — starting depth poll');
        start();
      } else {
        console.log('[gripper_reticle] not PLAYING (' + state + ') — stopping');
        stop();
      }
    }

    const pill = document.getElementById('statusPill');
    if (pill) {
      // Check initial state
      onPlayerStateChange(pill.dataset.state || '');
      // Watch for changes
      const mo = new MutationObserver((mutations) => {
        for (const m of mutations) {
          if (m.type === 'attributes' && m.attributeName === 'data-state') {
            onPlayerStateChange(pill.dataset.state || '');
          }
        }
      });
      mo.observe(pill, { attributes: true, attributeFilter: ['data-state'] });
    } else {
      console.warn('[gripper_reticle] statusPill not found, falling back to always-on');
      isPlaying = true;
      start();
    }

    document.addEventListener('visibilitychange', () => {
      if (!document.hidden && isPlaying && lastDepth !== null) tick();
    });

    window.addEventListener('resize', () => {
      if (isPlaying && lastDepth !== null) render(lastDepth, lastJawDepths.left, lastJawDepths.right);
    });

    console.log('[gripper_reticle] initialized', {
      fx, fy, cx, cy, imgW, imgH,
      offset: { Gx, Gy, Gz },
      jawSpacing, jawDiameter, depthScale, pollMs,
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initGripperReticle, { once: true });
  } else {
    initGripperReticle();
  }
})();
