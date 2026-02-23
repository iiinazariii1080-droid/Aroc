/**
 * scene-helpers.js — Visual debug helpers for the hybrid 3D scene
 * Version: 20260222b
 *
 * Provides:
 *   - World axes (RGB = XYZ in ROS convention, rendered in Three.js Y-up)
 *   - Arm base axes
 *   - TCP / flange axes
 *   - D435 camera mount group with frustum wireframe + camera-local axes
 *   - Camera mount point marker (sphere)
 *   - Live D435 depth point cloud (base64 uint16 via API gateway)
 *   - Fixed cloud orientation correction from config
 *   - Cloud record controls (Record/Pause + Clear map)
 *   - Coordinate HUD overlay
 *
 * Activated via URL query params (see SCENE_CONFIG.DEBUG_PARAMS):
 *   ?axes          — World origin + arm base axes
 *   ?axes=all      — + TCP + camera frame axes
 *   ?camera_frustum — D435 frustum wireframe on cameraMount + mount marker
 *   ?depth_cloud   — D435 live point cloud + world-map recording controls
 *   ?coords        — TCP coordinates HUD (ROS meters)
 *
 * Loaded as a plain <script> BEFORE arm3d-runtime.js.
 * Exposes window.SceneHelpers.
 * Requires: THREE (import-mapped), window.SCENE_CONFIG, window.CoordUtils
 */

(function () {
  "use strict";

  const CFG = window.SCENE_CONFIG;
  const CU  = window.CoordUtils;
  if (!CFG || !CU) {
    console.error("[scene-helpers] Missing SCENE_CONFIG or CoordUtils");
    return;
  }

  // Deferred THREE reference — set via init()
  let THREE = null;

  // Created objects registry for cleanup
  const createdObjects = [];

  // Ref to cameraMount group
  let cameraMountRef = null;
  const DEPTH_MAP_API_BASE = "/api/v1/depth_map";
  const DEPTH_MAP_MAGIC = [0x44, 0x4d, 0x50, 0x31]; // DMP1
  const DEPTH_MAP_VERSION = 1;
  const DEPTH_MAP_HEADER_BYTES = 12;
  const DEPTH_MAP_RECORD_BYTES = 15;

  // Depth overlay runtime state
  const depthState = {
    livePoints: null,
    liveGeometry: null,
    liveMaterial: null,
    mapPoints: null,
    mapGeometry: null,
    mapMaterial: null,
    voxelMap: new Map(),
    mapPointCount: 0,
    lastFetchMs: 0,
    lastSuccessMs: 0,
    inFlight: false,
    enabled: false,
    recording: false,
    frameWidth: 0,
    frameHeight: 0,
    livePointsCount: 0,
    fps: 0,
    status: "init",
    panelEl: null,
    panelEnabled: false,
    controlsEl: null,
    controlsEnabled: false,
    voxelSizeM: 0.005,
    maxVoxels: 400000,
    pendingShot: false,
    pendingShotColor: false,
    pendingClearFrame: false,
    persistStatus: "",
    persistenceLoaded: false,
    shotChunks: [],
    shotSeq: 0,
  };

  const BLACK_RGB_THRESHOLD = 12;

  function isNearBlackRgb(r8, g8, b8) {
    return r8 <= BLACK_RGB_THRESHOLD && g8 <= BLACK_RGB_THRESHOLD && b8 <= BLACK_RGB_THRESHOLD;
  }

  // ─────────────────────────────────────────────────
  // §1  Axes helper — ROS convention rendered in Three.js space
  // ─────────────────────────────────────────────────
  //
  // ROS axes colors:  X = Red, Y = Green, Z = Blue
  // Rendered in Three.js Y-up after coordinate swapping:
  //   ROS X (red)   → Three.js +X (right)
  //   ROS Y (green)  → Three.js -Z (into screen)
  //   ROS Z (blue)  → Three.js +Y (up)
  //

  /**
   * Create a labeled axis arrow.
   * @param {object} THREE_ - Three.js module
   * @param {THREE.Vector3} dir - direction
   * @param {THREE.Vector3} origin - origin
   * @param {number} length - world units
   * @param {number} color - hex color
   * @param {string} label - axis label text
   * @returns {THREE.Group}
   */
  function makeAxisArrow(THREE_, dir, origin, length, color, label) {
    const group = new THREE_.Group();

    // Arrow
    const arrow = new THREE_.ArrowHelper(
      dir.clone().normalize(), origin, length,
      color, length * 0.12, length * 0.06
    );
    group.add(arrow);

    // Text label (sprite)
    const canvas = document.createElement("canvas");
    canvas.width = 64;
    canvas.height = 32;
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#" + color.toString(16).padStart(6, "0");
    ctx.font = "bold 24px monospace";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(label, 32, 16);

    const tex = new THREE_.CanvasTexture(canvas);
    const mat = new THREE_.SpriteMaterial({ map: tex, depthTest: false });
    const sprite = new THREE_.Sprite(mat);
    sprite.scale.set(2, 1, 1);

    const labelPos = origin.clone().add(dir.clone().normalize().multiplyScalar(length * 1.15));
    sprite.position.copy(labelPos);
    group.add(sprite);

    return group;
  }


  /**
   * Create ROS-convention axes at a given origin in Three.js space.
   * Axes are 1m long (= 20 world units) by default.
   *
   * @param {object} THREE_ - Three.js module
   * @param {THREE.Vector3} [origin] - Three.js world-unit position
   * @param {number} [lengthM=1] - axis length in meters
   * @param {string} [prefix=""] - label prefix (e.g. "TCP " → "TCP X", "TCP Y", "TCP Z")
   * @returns {THREE.Group}
   */
  function createRosAxes(THREE_, origin, lengthM, prefix) {
    origin = origin || new THREE_.Vector3(0, 0, 0);
    lengthM = lengthM || 1;
    prefix = prefix || "";
    const len = CU.metersToWorld(lengthM);

    const group = new THREE_.Group();
    group.name = `ros_axes_${prefix || "world"}`;

    // ROS X (forward) → Three.js +X
    group.add(makeAxisArrow(THREE_,
      new THREE_.Vector3(1, 0, 0), origin, len, 0xff3333, prefix + "X"
    ));
    // ROS Y (left) → Three.js -Z
    group.add(makeAxisArrow(THREE_,
      new THREE_.Vector3(0, 0, -1), origin, len, 0x33ff33, prefix + "Y"
    ));
    // ROS Z (up) → Three.js +Y
    group.add(makeAxisArrow(THREE_,
      new THREE_.Vector3(0, 1, 0), origin, len, 0x3388ff, prefix + "Z"
    ));

    return group;
  }


  // ─────────────────────────────────────────────────
  // §2  D435 Camera mount + frustum
  // ─────────────────────────────────────────────────
  //
  // The D435 is mounted on the SIDE of the vacuum gripper, pointing
  // sideways (perpendicular to the gripper suction axis).
  //
  // Structure in scene graph:
  //   toolGroup  (= flange frame)
  //     └─ cameraMount  (positioned + rotated via TRANSFORMS.flangeToCamera)
  //          ├─ frustum wireframe  (extends along mount local -Z = camera optical axis)
  //          └─ camera-local axes  (cX right, cY down, cZ depth = -Z)
  //
  // flangeToCamera.ry = 90° rotates the mount's local -Z from
  // toolGroup's -Z to toolGroup's -X = sideways from the gripper.
  //

  /**
   * Create a cameraMount group with position/rotation from flangeToCamera config,
   * containing the frustum wireframe and camera-local axes.
   *
   * @param {object} THREE_ - Three.js module
   * @param {boolean} [includeFrustum=true]
   * @returns {THREE.Group} cameraMount group (attach to toolGroup)
   */
  function createCameraMount(THREE_, includeFrustum) {
    if (includeFrustum == null) includeFrustum = true;

    // Camera mount group: positioned and rotated per config
    const mount = new THREE_.Group();
    mount.name = "camera_mount_d435";
    applyCameraMountTransform(mount);

    // Mount point marker — small bright sphere showing where camera sits
    const markerGeo = new THREE_.SphereGeometry(CU.metersToWorld(0.008), 12, 8);
    const markerMat = new THREE_.MeshBasicMaterial({ color: 0xff00ff, transparent: true, opacity: 0.85 });
    const marker = new THREE_.Mesh(markerGeo, markerMat);
    marker.name = "cam_mount_marker";
    mount.add(marker);

    // Frustum wireframe inside mount (extends along mount's local -Z)
    if (includeFrustum) {
      mount.add(createFrustumGeometry(THREE_));
    }

    // Small camera-frame axes (0.05m) to show camera orientation
    mount.add(createCameraLocalAxes(THREE_, 0.05));

    cameraMountRef = mount;
    return mount;
  }

  function applyCameraMountTransform(targetMount) {
    if (!targetMount) return;
    const ftc = CFG.TRANSFORMS.flangeToCamera || {};
    const S = CFG.WORLD.SCALE_FACTOR;
    targetMount.position.set(
      (Number(ftc.tx) || 0) * S,
      (Number(ftc.ty) || 0) * S,
      (Number(ftc.tz) || 0) * S
    );
    targetMount.rotation.set(
      (Number(ftc.rx) || 0) * (Math.PI / 180),
      (Number(ftc.ry) || 0) * (Math.PI / 180),
      (Number(ftc.rz) || 0) * (Math.PI / 180),
      "XYZ"
    );
    targetMount.updateMatrix();
    targetMount.updateWorldMatrix(true, false);
  }


  /**
   * Create camera-local axes showing the camera's own coordinate frame:
   *   cam X (red)   = right in image     → mount local +X
   *   cam Y (green) = down in image      → mount local +Y
   *   cam Z (blue)  = depth/optical axis → mount local -Z (frustum direction)
   */
  function createCameraLocalAxes(THREE_, lengthM) {
    const len = CU.metersToWorld(lengthM);
    const group = new THREE_.Group();
    group.name = "cam_local_axes";

    // Camera X (right in image) = mount local +X → red
    group.add(makeAxisArrow(THREE_,
      new THREE_.Vector3(1, 0, 0), new THREE_.Vector3(0, 0, 0), len, 0xff4444, "cX"
    ));
    // Camera Y (down in image) = mount local +Y → green
    group.add(makeAxisArrow(THREE_,
      new THREE_.Vector3(0, 1, 0), new THREE_.Vector3(0, 0, 0), len, 0x44ff44, "cY"
    ));
    // Camera Z (depth/optical) = mount local -Z (frustum direction) → blue
    group.add(makeAxisArrow(THREE_,
      new THREE_.Vector3(0, 0, -1), new THREE_.Vector3(0, 0, 0), len, 0x4488ff, "cZ"
    ));

    return group;
  }


  /**
   * Create the raw frustum wireframe geometry.
   * Extends along LOCAL -Z of the parent (cameraMount).
   *
   * @param {object} THREE_ - Three.js module
   * @returns {THREE.LineSegments}
   */
  function createFrustumGeometry(THREE_) {
    const cam = CFG.DEPTH_CAMERA;
    const nearWU = CU.metersToWorld(cam.frustum.near);
    const farWU = CU.metersToWorld(cam.frustum.far);

    // Use canonical FOV
    const fov = CU.getCameraFOV();
    const halfH = Math.tan(fov.h * CU.DEG / 2);
    const halfV = Math.tan(fov.v * CU.DEG / 2);

    // Near and far plane half-sizes
    const nw = nearWU * halfH;
    const nh = nearWU * halfV;
    const fw = farWU * halfH;
    const fh = farWU * halfV;

    // Frustum extends along local -Z
    // Vertices: near plane 4 corners + far plane 4 corners
    const verts = new Float32Array([
      // near TL, TR, BR, BL
      -nw,  nh, -nearWU,
       nw,  nh, -nearWU,
       nw, -nh, -nearWU,
      -nw, -nh, -nearWU,
      // far TL, TR, BR, BL
      -fw,  fh, -farWU,
       fw,  fh, -farWU,
       fw, -fh, -farWU,
      -fw, -fh, -farWU,
    ]);

    // Line indices: near rect + far rect + 4 connecting edges
    const idx = new Uint16Array([
      0,1, 1,2, 2,3, 3,0,     // near plane
      4,5, 5,6, 6,7, 7,4,     // far plane
      0,4, 1,5, 2,6, 3,7,     // connecting
    ]);

    const geo = new THREE_.BufferGeometry();
    geo.setAttribute("position", new THREE_.BufferAttribute(verts, 3));
    geo.setIndex(new THREE_.BufferAttribute(idx, 1));

    const mat = new THREE_.LineBasicMaterial({
      color: 0x00ccaa,
      transparent: true,
      opacity: 0.6,
      depthTest: true,
    });

    const frustum = new THREE_.LineSegments(geo, mat);
    frustum.name = "d435_frustum";
    return frustum;
  }


  // ─────────────────────────────────────────────────
  // §3  Depth point cloud overlay
  // ─────────────────────────────────────────────────

  function decodeBase64ToUint16(base64) {
    const bin = atob(base64);
    const len = bin.length;
    if (len % 2 !== 0) {
      throw new Error("Depth base64 byte length is not even (uint16 expected)");
    }
    const bytes = new Uint8Array(len);
    for (let i = 0; i < len; i++) bytes[i] = bin.charCodeAt(i);
    const view = new DataView(bytes.buffer);
    const out = new Uint16Array(len / 2);
    for (let i = 0; i < out.length; i++) out[i] = view.getUint16(i * 2, true);
    return out;
  }

  function decodeBase64ToUint8(base64) {
    const bin = atob(base64);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }

  function ensureLiveDepthCloud() {
    if (depthState.livePoints) return depthState.livePoints;
    const overlay = CFG.DEPTH_OVERLAY || {};
    const rec = overlay.recording || {};
    const geo = new THREE.BufferGeometry();
    const mat = new THREE.PointsMaterial({
      size: rec.livePointSize_wu || overlay.pointSize_wu || 0.06,
      vertexColors: true,
      transparent: false,
      opacity: 1.0,
      depthTest: true,
      depthWrite: true,
      sizeAttenuation: true,
    });
    const points = new THREE.Points(geo, mat);
    points.name = "d435_depth_live_cloud";
    depthState.liveGeometry = geo;
    depthState.liveMaterial = mat;
    depthState.livePoints = points;
    createdObjects.push(points);
    return points;
  }

  function ensureMapDepthCloud() {
    if (depthState.mapPoints) return depthState.mapPoints;
    const overlay = CFG.DEPTH_OVERLAY || {};
    const rec = overlay.recording || {};
    const geo = new THREE.BufferGeometry();
    const mat = new THREE.PointsMaterial({
      size: rec.mapPointSize_wu || 0.04,
      vertexColors: true,
      transparent: false,
      opacity: 1.0,
      depthTest: true,
      depthWrite: true,
      sizeAttenuation: true,
    });
    const points = new THREE.Points(geo, mat);
    points.name = "d435_depth_map_cloud";
    depthState.mapGeometry = geo;
    depthState.mapMaterial = mat;
    depthState.mapPoints = points;
    createdObjects.push(points);
    return points;
  }

  async function fetchDepthFrameJson() {
    const overlay = CFG.DEPTH_OVERLAY || {};
    const url = overlay.frameJsonUrl || "/api/v1/depth_camera/depth/frame?format=json";
    const r = await fetch(url, { cache: "no-store" });
    if (!r.ok) {
      throw new Error(`Depth fetch failed: HTTP ${r.status}`);
    }
    return r.json();
  }

  /**
   * Fetch the aligned RGBD payload from frame_color_overlay.
   * Returns JSON with rgb_data (RGB24) + depth_data (Z16) + width/height/timestamp.
   */
  async function fetchAlignedRgbdJson() {
    const overlay = CFG.DEPTH_OVERLAY || {};
    const url = overlay.frameColorOverlayJsonUrl || "/api/v1/depth_camera/depth/frame_color_overlay?format=json";
    const r = await fetch(url, { cache: "no-store" });
    if (!r.ok) {
      throw new Error(`Aligned RGBD fetch failed: HTTP ${r.status}`);
    }
    return r.json();
  }

  function ensureDepthDebugPanel() {
    return null;
  }

  function updateDepthDebugPanel() {
    return;
  }

  function updateCloudControlPanel() {
    if (!depthState.controlsEl) return;
    const btn = depthState.controlsEl.querySelector("button[data-role='record']");
    const meta = depthState.controlsEl.querySelector("div[data-role='meta']");
    if (btn) btn.textContent = depthState.recording ? "Pause Record" : "Start Record";
    if (meta) {
      let shotsCount = 0;
      for (const chunk of depthState.shotChunks) {
        if (chunk && chunk.kind === "shot") shotsCount += 1;
      }
      const base = `Map: ${depthState.mapPointCount}  |  ${depthState.recording ? "REC" : "PAUSE"}  |  Shots: ${shotsCount}`;
      meta.textContent = depthState.persistStatus ? `${base}\n${depthState.persistStatus}` : base;
    }
  }

  function encodeDepthMapBinary() {
    const entries = Array.from(depthState.voxelMap.values());
    const count = entries.length;
    const buffer = new ArrayBuffer(DEPTH_MAP_HEADER_BYTES + count * DEPTH_MAP_RECORD_BYTES);
    const view = new DataView(buffer);

    view.setUint8(0, DEPTH_MAP_MAGIC[0]);
    view.setUint8(1, DEPTH_MAP_MAGIC[1]);
    view.setUint8(2, DEPTH_MAP_MAGIC[2]);
    view.setUint8(3, DEPTH_MAP_MAGIC[3]);
    view.setUint16(4, DEPTH_MAP_VERSION, true);
    view.setUint16(6, 0, true);
    view.setUint32(8, count, true);

    let offset = DEPTH_MAP_HEADER_BYTES;
    for (const entry of entries) {
      view.setFloat32(offset, Number(entry.x) || 0, true); offset += 4;
      view.setFloat32(offset, Number(entry.y) || 0, true); offset += 4;
      view.setFloat32(offset, Number(entry.z) || 0, true); offset += 4;
      view.setUint8(offset, Math.max(0, Math.min(255, Math.round((Number(entry.r) || 0) * 255)))); offset += 1;
      view.setUint8(offset, Math.max(0, Math.min(255, Math.round((Number(entry.g) || 0) * 255)))); offset += 1;
      view.setUint8(offset, Math.max(0, Math.min(255, Math.round((Number(entry.b) || 0) * 255)))); offset += 1;
    }

    return buffer;
  }

  function decodeDepthMapBinary(buffer) {
    const view = new DataView(buffer);
    if (view.byteLength < DEPTH_MAP_HEADER_BYTES) {
      throw new Error("Saved depth map payload is too small");
    }
    if (
      view.getUint8(0) !== DEPTH_MAP_MAGIC[0] ||
      view.getUint8(1) !== DEPTH_MAP_MAGIC[1] ||
      view.getUint8(2) !== DEPTH_MAP_MAGIC[2] ||
      view.getUint8(3) !== DEPTH_MAP_MAGIC[3]
    ) {
      throw new Error("Saved depth map has invalid magic header");
    }

    const version = view.getUint16(4, true);
    if (version !== DEPTH_MAP_VERSION) {
      throw new Error(`Unsupported depth map version: ${version}`);
    }

    const count = view.getUint32(8, true);
    const expected = DEPTH_MAP_HEADER_BYTES + count * DEPTH_MAP_RECORD_BYTES;
    if (view.byteLength !== expected) {
      throw new Error(`Saved depth map size mismatch: got ${view.byteLength}, expected ${expected}`);
    }

    const entries = new Array(count);
    let offset = DEPTH_MAP_HEADER_BYTES;
    for (let i = 0; i < count; i++) {
      const x = view.getFloat32(offset, true); offset += 4;
      const y = view.getFloat32(offset, true); offset += 4;
      const z = view.getFloat32(offset, true); offset += 4;
      const r = view.getUint8(offset) / 255; offset += 1;
      const g = view.getUint8(offset) / 255; offset += 1;
      const b = view.getUint8(offset) / 255; offset += 1;
      entries[i] = { x, y, z, r, g, b };
    }
    return entries;
  }

  function applyLoadedMapEntries(entries) {
    depthState.voxelMap.clear();
    depthState.shotChunks.length = 0;
    depthState.shotSeq = 0;
    const step = depthState.voxelSizeM * CFG.WORLD.SCALE_FACTOR;
    for (const entry of entries) {
      const qx = Math.round(entry.x / step);
      const qy = Math.round(entry.y / step);
      const qz = Math.round(entry.z / step);
      const key = `${qx}|${qy}|${qz}`;
      depthState.voxelMap.set(key, entry);
    }
    rebuildMapGeometryFromVoxelMap();
    updateCloudControlPanel();
  }

  async function savePersistedMap() {
    try {
      const payload = encodeDepthMapBinary();
      const r = await fetch(`${DEPTH_MAP_API_BASE}/save`, {
        method: "POST",
        headers: {
          "Content-Type": "application/octet-stream",
          "X-Depth-Map-Voxel-Size-Mm": String(Math.round(depthState.voxelSizeM * 1000)),
        },
        body: payload,
      });
      if (!r.ok) {
        throw new Error(`HTTP ${r.status}`);
      }
      const info = await r.json();
      depthState.persistStatus = `Saved: ${info.point_count ?? depthState.mapPointCount} pts`;
    } catch (e) {
      depthState.persistStatus = `Save error: ${e?.message || e}`;
    }
    updateCloudControlPanel();
  }

  async function loadPersistedMap() {
    try {
      const r = await fetch(`${DEPTH_MAP_API_BASE}/load`, { cache: "no-store" });
      if (r.status === 404) {
        depthState.persistStatus = "Saved map: none";
        updateCloudControlPanel();
        return;
      }
      if (!r.ok) {
        throw new Error(`HTTP ${r.status}`);
      }
      const payload = await r.arrayBuffer();
      const entries = decodeDepthMapBinary(payload);
      applyLoadedMapEntries(entries);
      depthState.persistStatus = `Loaded: ${entries.length} pts`;
    } catch (e) {
      depthState.persistStatus = `Load error: ${e?.message || e}`;
    }
    updateCloudControlPanel();
  }

  async function clearPersistedMap() {
    try {
      const r = await fetch(DEPTH_MAP_API_BASE, { method: "DELETE" });
      if (!r.ok) {
        throw new Error(`HTTP ${r.status}`);
      }
      depthState.persistStatus = "Saved map cleared";
    } catch (e) {
      depthState.persistStatus = `Clear saved error: ${e?.message || e}`;
    }
    updateCloudControlPanel();
  }

  function clearRecordedMap() {
    depthState.voxelMap.clear();
    depthState.shotChunks.length = 0;
    depthState.shotSeq = 0;
    depthState.mapPointCount = 0;
    if (depthState.mapGeometry) {
      depthState.mapGeometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array(0), 3));
      depthState.mapGeometry.setAttribute("color", new THREE.BufferAttribute(new Float32Array(0), 3));
      depthState.mapGeometry.computeBoundingSphere();
    }
    updateCloudControlPanel();
    updateDepthDebugPanel();
  }

  function ensureCloudControlPanel() {
    if (depthState.controlsEl) return depthState.controlsEl;
    const panel = document.createElement("div");
    panel.id = "arm3d-cloud-controls";
    panel.style.cssText = [
      "position:fixed",
      "right:12px",
      "top:12px",
      "width:210px",
      "padding:10px 12px",
      "border-radius:8px",
      "background:rgba(0,0,0,0.62)",
      "color:#e5e7eb",
      "font-family:system-ui,-apple-system,sans-serif",
      "font-size:12px",
      "z-index:14",
      "user-select:none",
    ].join(";");

    const title = document.createElement("div");
    title.textContent = "Cloud Record";
    title.style.cssText = "font-weight:600;margin:0 0 8px 0;color:#e5e7eb;";
    panel.appendChild(title);

    const row = document.createElement("div");
    row.style.cssText = "display:flex;gap:8px;";

    const recBtn = document.createElement("button");
    recBtn.type = "button";
    recBtn.dataset.role = "record";
    recBtn.style.cssText = "flex:1;border:1px solid #4b5563;background:#111827;color:#e5e7eb;border-radius:4px;padding:4px 8px;font-size:11px;cursor:pointer;";
    recBtn.addEventListener("click", () => {
      depthState.recording = !depthState.recording;
      depthState.status = depthState.recording ? "record" : "paused";
      updateCloudControlPanel();
      updateDepthDebugPanel();
    });

    const clearBtn = document.createElement("button");
    clearBtn.type = "button";
    clearBtn.textContent = "Clear";
    clearBtn.style.cssText = "flex:1;border:1px solid #4b5563;background:#111827;color:#e5e7eb;border-radius:4px;padding:4px 8px;font-size:11px;cursor:pointer;";
    clearBtn.addEventListener("click", () => {
      depthState.pendingClearFrame = true;
      if (!depthState.recording) depthState.status = "clear-frame";
      updateCloudControlPanel();
    });

    const shotBtn = document.createElement("button");
    shotBtn.type = "button";
    shotBtn.textContent = "Shot";
    shotBtn.style.cssText = "flex:1;border:1px solid #4b5563;background:#111827;color:#e5e7eb;border-radius:4px;padding:4px 8px;font-size:11px;cursor:pointer;";
    shotBtn.addEventListener("click", () => {
      depthState.pendingShot = true;
      if (!depthState.recording) depthState.status = "shot";
      updateCloudControlPanel();
    });

    const shotColorBtn = document.createElement("button");
    shotColorBtn.type = "button";
    shotColorBtn.textContent = "Shot Color";
    shotColorBtn.style.cssText = "flex:1;border:1px solid #4b5563;background:#111827;color:#e5e7eb;border-radius:4px;padding:4px 8px;font-size:11px;cursor:pointer;";
    shotColorBtn.addEventListener("click", () => {
      depthState.pendingShotColor = true;
      if (!depthState.recording) depthState.status = "shot-color";
      updateCloudControlPanel();
    });

    row.appendChild(recBtn);
    row.appendChild(shotBtn);
    row.appendChild(shotColorBtn);
    row.appendChild(clearBtn);
    panel.appendChild(row);

    const persistRow = document.createElement("div");
    persistRow.style.cssText = "display:flex;gap:8px;margin-top:8px;";

    const saveBtn = document.createElement("button");
    saveBtn.type = "button";
    saveBtn.textContent = "Save";
    saveBtn.style.cssText = "flex:1;border:1px solid #4b5563;background:#111827;color:#e5e7eb;border-radius:4px;padding:4px 8px;font-size:11px;cursor:pointer;";
    saveBtn.addEventListener("click", () => {
      savePersistedMap();
    });

    const loadBtn = document.createElement("button");
    loadBtn.type = "button";
    loadBtn.textContent = "Load";
    loadBtn.style.cssText = "flex:1;border:1px solid #4b5563;background:#111827;color:#e5e7eb;border-radius:4px;padding:4px 8px;font-size:11px;cursor:pointer;";
    loadBtn.addEventListener("click", () => {
      loadPersistedMap();
    });

    const clearSavedBtn = document.createElement("button");
    clearSavedBtn.type = "button";
    clearSavedBtn.textContent = "Clear Saved";
    clearSavedBtn.style.cssText = "flex:1;border:1px solid #4b5563;background:#111827;color:#e5e7eb;border-radius:4px;padding:4px 8px;font-size:11px;cursor:pointer;";
    clearSavedBtn.addEventListener("click", () => {
      clearPersistedMap();
    });

    persistRow.appendChild(saveBtn);
    persistRow.appendChild(loadBtn);
    persistRow.appendChild(clearSavedBtn);
    panel.appendChild(persistRow);

    const meta = document.createElement("div");
    meta.dataset.role = "meta";
    meta.style.cssText = "margin-top:8px;color:#93c5fd;font-family:monospace;font-size:11px;white-space:pre;";
    panel.appendChild(meta);

    document.body.appendChild(panel);
    depthState.controlsEl = panel;
    depthState.controlsEnabled = true;
    updateCloudControlPanel();
    return panel;
  }

  function toDepthPayload(json) {
    const base64 = json?.data || json?.depth || json?.frame;
    if (!base64 || typeof base64 !== "string") {
      throw new Error("Depth JSON has no base64 payload in data/depth/frame");
    }
    const width = Number(json?.width) || CFG.DEPTH_CAMERA.intrinsics.width;
    const height = Number(json?.height) || CFG.DEPTH_CAMERA.intrinsics.height;
    const raw = decodeBase64ToUint16(base64);
    if (raw.length !== width * height) {
      throw new Error(`Depth payload size mismatch: ${raw.length} vs ${width}x${height}`);
    }
    return { raw, width, height };
  }

  function toColorOverlayPayload(json) {
    const base64 = json?.data || json?.rgb || json?.frame;
    if (!base64 || typeof base64 !== "string") {
      throw new Error("Color overlay JSON has no base64 payload in data/rgb/frame");
    }
    const width = Number(json?.width);
    const height = Number(json?.height);
    if (!width || !height) {
      throw new Error("Color overlay JSON has no valid width/height");
    }
    const raw = decodeBase64ToUint8(base64);
    const expected = width * height * 3;
    if (raw.length !== expected) {
      throw new Error(`Color overlay payload size mismatch: ${raw.length} vs ${width}x${height}x3`);
    }
    return { raw, width, height };
  }

  function buildDepthFrame(raw, width, height, colorOverlay) {
    const overlay = CFG.DEPTH_OVERLAY || {};
    const swapPayloadWH = !!overlay.swapPayloadWH;
    const pixelRotationDeg = Number(overlay.pixelRotationDeg) || 0;
    const stride = Math.max(1, Number(overlay.stridePx) || 4);
    const minRaw = Math.max(0, Number(overlay.minRaw) || 50);
    const maxRaw = Math.max(minRaw + 1, Number(overlay.maxRaw) || 2000);
    const minDistanceM = Math.max(0, Number(overlay.minDistanceM) || 0.10);
    const flipX = !!overlay.flipX;
    const flipY = !!overlay.flipY;
    const logicalWidth = swapPayloadWH ? height : width;
    const logicalHeight = swapPayloadWH ? width : height;
    const mirrorVertical = !!overlay.cloudMirrorVertical;
    const mirrorAxis = (overlay.cloudMirrorAxis || "y").toLowerCase();
    const rotCfg = overlay.cloudRotationDeg || { rx: 0, ry: 0, rz: 0 };
    const rotM = new THREE.Matrix4().makeRotationFromEuler(new THREE.Euler(
      (Number(rotCfg.rx) || 0) * (Math.PI / 180),
      (Number(rotCfg.ry) || 0) * (Math.PI / 180),
      (Number(rotCfg.rz) || 0) * (Math.PI / 180),
      "XYZ"
    ));
    const p = new THREE.Vector3();
    const colorRaw = colorOverlay?.raw || null;
    const colorWidth = Number(colorOverlay?.width) || 0;
    const colorHeight = Number(colorOverlay?.height) || 0;
    const normDeg = ((((pixelRotationDeg % 360) + 360) % 360) === 270) ? -90 : (((pixelRotationDeg % 360) + 360) % 360);
    const cols = Math.ceil(logicalWidth / stride);
    const rows = Math.ceil(logicalHeight / stride);
    const maxPoints = cols * rows;
    const positions = new Float32Array(maxPoints * 3);
    const colors = new Float32Array(maxPoints * 3);

    let count = 0;
    for (let v = 0; v < logicalHeight; v += stride) {
      for (let u = 0; u < logicalWidth; u += stride) {
        const srcU = flipX ? (logicalWidth - 1 - u) : u;
        const srcV = flipY ? (logicalHeight - 1 - v) : v;
        const rawIndex = swapPayloadWH
          ? (srcV * height + srcU)
          : (srcV * width + srcU);
        const rawDepth = raw[rawIndex];
        if (!rawDepth || rawDepth < minRaw || rawDepth > maxRaw) continue;

        let intrU = srcU;
        let intrV = srcV;
        if (normDeg === 90) {
          intrU = srcV;
          intrV = (logicalWidth - 1 - srcU);
        } else if (normDeg === -90) {
          intrU = (logicalHeight - 1 - srcV);
          intrV = srcU;
        } else if (normDeg === 180) {
          intrU = (logicalWidth - 1 - srcU);
          intrV = (logicalHeight - 1 - srcV);
        }

        const cam = CU.depthPixelToCamera(intrU, intrV, rawDepth);
        if (!Number.isFinite(cam.z) || cam.z < minDistanceM) continue;
        p.set(cam.x, cam.y, -cam.z).applyMatrix4(rotM);
        if (mirrorVertical) {
          if (mirrorAxis === "x") p.x = -p.x;
          else if (mirrorAxis === "z") p.z = -p.z;
          else p.y = -p.y;
        }
        const x = CU.metersToWorld(p.x);
        const y = CU.metersToWorld(p.y);
        const z = CU.metersToWorld(p.z);

        const i3 = count * 3;
        positions[i3 + 0] = x;
        positions[i3 + 1] = y;
        positions[i3 + 2] = z;

        if (colorRaw && colorWidth > 0 && colorHeight > 0) {
          const cu = logicalWidth > 1
            ? Math.max(0, Math.min(colorWidth - 1, Math.round((srcU / (logicalWidth - 1)) * (colorWidth - 1))))
            : 0;
          const cv = logicalHeight > 1
            ? Math.max(0, Math.min(colorHeight - 1, Math.round((srcV / (logicalHeight - 1)) * (colorHeight - 1))))
            : 0;
          const ci = (cv * colorWidth + cu) * 3;
          const r8 = colorRaw[ci + 0];
          const g8 = colorRaw[ci + 1];
          const b8 = colorRaw[ci + 2];
          if (isNearBlackRgb(r8, g8, b8)) {
            continue;
          } else {
            colors[i3 + 0] = r8 / 255;
            colors[i3 + 1] = g8 / 255;
            colors[i3 + 2] = b8 / 255;
          }
        } else {
          // Uniform single colour for depth-only shots (configurable in scene-config)
          const uniColor = overlay.depthShotColor || [0.45, 0.75, 0.95];
          colors[i3 + 0] = uniColor[0];
          colors[i3 + 1] = uniColor[1];
          colors[i3 + 2] = uniColor[2];
        }
        count += 1;
      }
    }

    return {
      positions: positions.subarray(0, count * 3),
      colors: colors.subarray(0, count * 3),
      count,
      width: logicalWidth,
      height: logicalHeight,
    };
  }

  function applyLiveGeometry(frame) {
    if (!depthState.liveGeometry) return;
    depthState.liveGeometry.setAttribute("position", new THREE.BufferAttribute(frame.positions, 3));
    depthState.liveGeometry.setAttribute("color", new THREE.BufferAttribute(frame.colors, 3));
    depthState.liveGeometry.computeBoundingSphere();
    depthState.frameWidth = frame.width;
    depthState.frameHeight = frame.height;
    depthState.livePointsCount = frame.count;
  }

  function clearLiveGeometry() {
    if (!depthState.liveGeometry) return;
    depthState.liveGeometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array(0), 3));
    depthState.liveGeometry.setAttribute("color", new THREE.BufferAttribute(new Float32Array(0), 3));
    depthState.liveGeometry.computeBoundingSphere();
    depthState.frameWidth = 0;
    depthState.frameHeight = 0;
    depthState.livePointsCount = 0;
  }

  function rebuildMapGeometryFromVoxelMap() {
    if (!depthState.mapGeometry) return;
    const count = depthState.voxelMap.size;
    const positions = new Float32Array(count * 3);
    const colors = new Float32Array(count * 3);
    let i = 0;
    for (const entry of depthState.voxelMap.values()) {
      const i3 = i * 3;
      positions[i3 + 0] = entry.x;
      positions[i3 + 1] = entry.y;
      positions[i3 + 2] = entry.z;
      colors[i3 + 0] = entry.r;
      colors[i3 + 1] = entry.g;
      colors[i3 + 2] = entry.b;
      i += 1;
    }
    depthState.mapGeometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    depthState.mapGeometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
    depthState.mapGeometry.computeBoundingSphere();
    depthState.mapPointCount = count;
  }

  function buildShotChunkFromFrame(frame, cameraWorldMatrix) {
    const step = depthState.voxelSizeM * CFG.WORLD.SCALE_FACTOR;
    const maxVoxels = depthState.maxVoxels;
    const m = cameraWorldMatrix || cameraMountRef.matrixWorld;
    const p = new THREE.Vector3();
    const points = new Map();

    for (let i = 0; i < frame.count; i++) {
      const i3 = i * 3;
      p.set(frame.positions[i3 + 0], frame.positions[i3 + 1], frame.positions[i3 + 2]);
      p.applyMatrix4(m);
      const qx = Math.round(p.x / step);
      const qy = Math.round(p.y / step);
      const qz = Math.round(p.z / step);
      const key = `${qx}|${qy}|${qz}`;
      if (points.has(key)) continue;
      points.set(key, {
        x: p.x,
        y: p.y,
        z: p.z,
        r: frame.colors[i3 + 0],
        g: frame.colors[i3 + 1],
        b: frame.colors[i3 + 2],
      });
      if (points.size >= maxVoxels) break;
    }

    return {
      id: ++depthState.shotSeq,
      kind: "shot",
      points,
    };
  }

  function mergeShotChunks() {
    const merged = new Map();
    for (const chunk of depthState.shotChunks) {
      for (const [key, entry] of chunk.points.entries()) {
        merged.set(key, entry);
      }
    }
    return merged;
  }

  function seedShotChunksFromCurrentMap() {
    if (depthState.shotChunks.length > 0) return;
    if (!depthState.voxelMap.size) return;
    const baseline = new Map();
    for (const [key, entry] of depthState.voxelMap.entries()) {
      baseline.set(key, entry);
    }
    depthState.shotChunks.push({ id: ++depthState.shotSeq, kind: "baseline", points: baseline });
  }

  function accumulateShotWithRing(frame, cameraWorldMatrix) {
    seedShotChunksFromCurrentMap();
    const nextChunk = buildShotChunkFromFrame(frame, cameraWorldMatrix);
    if (!nextChunk.points.size) {
      rebuildMapGeometryFromVoxelMap();
      updateCloudControlPanel();
      return;
    }

    depthState.shotChunks.push(nextChunk);
    let merged = mergeShotChunks();
    let removed = 0;
    while (merged.size > depthState.maxVoxels && depthState.shotChunks.length > 1) {
      depthState.shotChunks.shift();
      removed += 1;
      merged = mergeShotChunks();
    }

    depthState.voxelMap = merged;
    if (removed > 0) {
      depthState.persistStatus = `Ring: dropped ${removed} oldest shot`;
    }
    rebuildMapGeometryFromVoxelMap();
    updateCloudControlPanel();
  }

  function accumulateFrameToMap(frame, cameraWorldMatrix, forceCapture) {
    if (!cameraMountRef || !depthState.mapGeometry) return;
    if (!depthState.recording && !forceCapture) return;

    if (forceCapture) {
      accumulateShotWithRing(frame, cameraWorldMatrix);
      return;
    }

    depthState.shotChunks.length = 0;
    const step = depthState.voxelSizeM * CFG.WORLD.SCALE_FACTOR;
    const maxVoxels = depthState.maxVoxels;
    const m = cameraWorldMatrix || cameraMountRef.matrixWorld;
    const p = new THREE.Vector3();

    for (let i = 0; i < frame.count; i++) {
      const i3 = i * 3;
      p.set(frame.positions[i3 + 0], frame.positions[i3 + 1], frame.positions[i3 + 2]);
      p.applyMatrix4(m);

      const qx = Math.round(p.x / step);
      const qy = Math.round(p.y / step);
      const qz = Math.round(p.z / step);
      const key = `${qx}|${qy}|${qz}`;
      if (depthState.voxelMap.has(key)) continue;
      if (depthState.voxelMap.size >= maxVoxels) break;

      depthState.voxelMap.set(key, {
        x: p.x,
        y: p.y,
        z: p.z,
        r: frame.colors[i3 + 0],
        g: frame.colors[i3 + 1],
        b: frame.colors[i3 + 2],
      });
    }

    rebuildMapGeometryFromVoxelMap();
    updateCloudControlPanel();
  }

  function clearFrameRegion(frame, cameraWorldMatrix) {
    if (!frame || !frame.count) return;
    const minDistanceM = Math.max(0, Number((CFG.DEPTH_OVERLAY || {}).minDistanceM) || 0.10);
    let tanXMax = 0;
    let tanYMax = 0;
    for (let i = 0; i < frame.count; i++) {
      const i3 = i * 3;
      const x = frame.positions[i3 + 0] / CFG.WORLD.SCALE_FACTOR;
      const y = frame.positions[i3 + 1] / CFG.WORLD.SCALE_FACTOR;
      const z = frame.positions[i3 + 2] / CFG.WORLD.SCALE_FACTOR;
      const dist = -z;
      if (dist <= minDistanceM) continue;
      tanXMax = Math.max(tanXMax, Math.abs(x) / Math.max(1e-6, dist));
      tanYMax = Math.max(tanYMax, Math.abs(y) / Math.max(1e-6, dist));
    }
    if (tanXMax <= 0 || tanYMax <= 0) return;

    const invCamera = (cameraWorldMatrix || cameraMountRef.matrixWorld).clone().invert();
    const p = new THREE.Vector3();
    const keysToDelete = new Set();
    for (const [key, entry] of depthState.voxelMap.entries()) {
      p.set(entry.x, entry.y, entry.z).applyMatrix4(invCamera);
      const xM = p.x / CFG.WORLD.SCALE_FACTOR;
      const yM = p.y / CFG.WORLD.SCALE_FACTOR;
      const zM = p.z / CFG.WORLD.SCALE_FACTOR;
      const dist = -zM;
      if (dist <= minDistanceM) continue;
      const tx = Math.abs(xM) / Math.max(1e-6, dist);
      const ty = Math.abs(yM) / Math.max(1e-6, dist);
      if (tx <= tanXMax && ty <= tanYMax) {
        keysToDelete.add(key);
      }
    }

    let removed = 0;
    for (const key of keysToDelete) {
      if (depthState.voxelMap.delete(key)) removed += 1;
    }
    if (keysToDelete.size > 0) {
      for (const chunk of depthState.shotChunks) {
        for (const key of keysToDelete) chunk.points.delete(key);
      }
      depthState.shotChunks = depthState.shotChunks.filter(chunk => chunk.points.size > 0);
    }

    if (removed > 0) {
      depthState.persistStatus = `Clear frame: removed ${removed} voxels`;
    }
    rebuildMapGeometryFromVoxelMap();
    updateCloudControlPanel();
    updateDepthDebugPanel();
  }

  async function updateDepthOverlay(nowMs) {
    if (!depthState.enabled || !cameraMountRef || !depthState.livePoints) return;
    const wantsCapture = depthState.recording || depthState.pendingShot || depthState.pendingShotColor || depthState.pendingClearFrame;
    if (!wantsCapture) {
      clearLiveGeometry();
      if (depthState.status !== "paused") {
        depthState.status = "paused";
        updateDepthDebugPanel();
      }
      return;
    }
    const overlay = CFG.DEPTH_OVERLAY || {};
    const intervalMs = 1000 / Math.max(1, Number(overlay.fps) || 2);
    if (depthState.inFlight) return;
    if (depthState.recording && (nowMs - depthState.lastFetchMs) < intervalMs) return;

    depthState.inFlight = true;
    depthState.status = "fetch";
    updateDepthDebugPanel();
    depthState.lastFetchMs = nowMs;
    try {
      const consumeShot = depthState.pendingShot;
      const consumeColorShot = depthState.pendingShotColor;
      const consumeClearFrame = depthState.pendingClearFrame;
      depthState.pendingShot = false;
      depthState.pendingShotColor = false;
      depthState.pendingClearFrame = false;
      cameraMountRef.updateWorldMatrix(true, false);
      const cameraWorldSnapshot = cameraMountRef.matrixWorld.clone();

      if (consumeClearFrame) {
        const json = await fetchDepthFrameJson();
        const { raw, width, height } = toDepthPayload(json);
        const frame = buildDepthFrame(raw, width, height);
        clearLiveGeometry();
        clearFrameRegion(frame, cameraWorldSnapshot);
        depthState.status = "clear-frame";
        updateCloudControlPanel();
        updateDepthDebugPanel();
        return;
      }

      // Shot         → depth only, uniform colour (depthShotColor)
      // Shot Color   → native depth geometry + RGB colours from frame_color_overlay
      // Record       → depth only (or +colour if alwaysFetchColor)
      let frame;
      if (consumeColorShot) {
        // Keep geometry identical to Shot: depth always comes from native /depth/frame
        let colorOverlay = null;
        let depthJson;
        try {
          const [depthResp, rgbd] = await Promise.all([
            fetchDepthFrameJson(),
            fetchAlignedRgbdJson(),
          ]);
          depthJson = depthResp;
          colorOverlay = toColorOverlayPayload({
            data: rgbd.rgb_data,
            width: rgbd.width,
            height: rgbd.height,
          });
        } catch (colorErr) {
          console.warn("[scene-helpers] colour fetch failed, using uniform:", colorErr?.message || colorErr);
          if (!depthJson) depthJson = await fetchDepthFrameJson();
          colorOverlay = null;
        }
        const { raw, width, height } = toDepthPayload(depthJson);
        frame = buildDepthFrame(raw, width, height, colorOverlay);
      } else {
        // Shot or Record — depth only, uniform colour
        const json = await fetchDepthFrameJson();
        const { raw, width, height } = toDepthPayload(json);
        frame = buildDepthFrame(raw, width, height);
      }
      applyLiveGeometry(frame);
      accumulateFrameToMap(frame, cameraWorldSnapshot, consumeShot || consumeColorShot);
      if (depthState.lastSuccessMs > 0) {
        const dt = Math.max(1, nowMs - depthState.lastSuccessMs);
        const instantFps = 1000 / dt;
        depthState.fps = depthState.fps > 0
          ? (depthState.fps * 0.7 + instantFps * 0.3)
          : instantFps;
      }
      depthState.lastSuccessMs = nowMs;
      depthState.status = depthState.recording ? "ok" : (consumeColorShot ? "shot-color" : (consumeShot ? "shot" : "paused"));
      updateCloudControlPanel();
      updateDepthDebugPanel();
    } catch (e) {
      depthState.status = "error";
      updateDepthDebugPanel();
      console.warn("[scene-helpers] depth overlay update failed:", e?.message || e);
    } finally {
      depthState.inFlight = false;
    }
  }

  function setDepthPointSize(sizeWu) {
    const size = Math.max(0.01, Math.min(0.5, Number(sizeWu) || 0.06));
    const overlay = CFG.DEPTH_OVERLAY || {};
    overlay.pointSize_wu = size;
    if (overlay.recording) overlay.recording.livePointSize_wu = size;
    if (depthState.liveMaterial) {
      depthState.liveMaterial.size = size;
      depthState.liveMaterial.needsUpdate = true;
    }
    if (depthState.mapMaterial) {
      depthState.mapMaterial.size = size;
      depthState.mapMaterial.needsUpdate = true;
    }
  }


  // ─────────────────────────────────────────────────
  // §4  Coordinates HUD
  // ─────────────────────────────────────────────────

  let coordsHudEl = null;

  /**
   * Create or get the coordinates HUD element.
   * @returns {HTMLElement}
   */
  function ensureCoordsHUD() {
    if (coordsHudEl) return coordsHudEl;
    coordsHudEl = document.createElement("div");
    coordsHudEl.id = "arm3d-coords-hud";
    coordsHudEl.style.cssText = [
      "position:fixed",
      "left:12px",
      "top:12px",
      "padding:8px 12px",
      "border-radius:6px",
      "background:rgba(0,0,0,0.6)",
      "color:#a5f3c4",
      "font-family:monospace",
      "font-size:12px",
      "line-height:1.5",
      "z-index:12",
      "user-select:none",
      "pointer-events:none",
      "white-space:pre",
    ].join(";");
    document.body.appendChild(coordsHudEl);
    return coordsHudEl;
  }

  /**
   * Update the coordinates HUD with TCP world position.
   * Call this from the animation loop.
   *
   * @param {THREE.Vector3} tcpWorldPos - TCP position in Three.js world units
   * @param {number[]} [joints] - joint angles in degrees (6 values)
  * @param {number} [liftMU] - lift motor units
  * @param {object} [robotPose] - robot pose from symovo ({xM, yM, thetaDeg, mapId})
  * @param {object} [rootPose] - root group world pose ({xWu, yWu, zWu, yawYDeg})
   */
  function updateCoordsHUD(tcpWorldPos, joints, liftMU, robotPose, rootPose) {
    const el = ensureCoordsHUD();
    if (!tcpWorldPos) {
      el.textContent = "TCP: no data";
      return;
    }

    const [rx, ry, rz] = CU.threeWorldToRosMm(tcpWorldPos.x, tcpWorldPos.y, tcpWorldPos.z);
    const liftM = liftMU != null ? CFG.LIFT.toMetersY(liftMU) : 0;

    let text = `TCP (ROS mm): ${rx.toFixed(1)}, ${ry.toFixed(1)}, ${rz.toFixed(1)}`;
    text += `\nTCP (wu):  ${tcpWorldPos.x.toFixed(2)}, ${tcpWorldPos.y.toFixed(2)}, ${tcpWorldPos.z.toFixed(2)}`;
    if (liftMU != null) {
      text += `\nLift: ${liftMU} mU = ${(liftM * 1000).toFixed(1)} mm`;
    }
    if (joints && joints.length) {
      text += `\nJ: ${joints.map(j => (j||0).toFixed(1)).join(", ")}`;
    }
    if (robotPose && Number.isFinite(robotPose.xM) && Number.isFinite(robotPose.yM)) {
      text += `\nRobot (ROS m): ${robotPose.xM.toFixed(3)}, ${robotPose.yM.toFixed(3)}`;
      if (Number.isFinite(robotPose.thetaDeg)) {
        text += `  Yaw: ${robotPose.thetaDeg.toFixed(2)}°`;
      }
      if (Number.isFinite(robotPose.mapId)) {
        text += `  map_id: ${robotPose.mapId}`;
      }
    }
    if (rootPose && Number.isFinite(rootPose.xWu) && Number.isFinite(rootPose.yWu) && Number.isFinite(rootPose.zWu)) {
      text += `\nRobot root (wu): ${rootPose.xWu.toFixed(2)}, ${rootPose.yWu.toFixed(2)}, ${rootPose.zWu.toFixed(2)}`;
      if (Number.isFinite(rootPose.yawYDeg)) {
        text += `  RotY: ${rootPose.yawYDeg.toFixed(2)}°`;
      }
    }
    el.textContent = text;
  }


  // ─────────────────────────────────────────────────
  // §6  Init — called from arm3d-runtime after THREE is available
  // ─────────────────────────────────────────────────

  /**
   * Initialize scene helpers. Call once after Three.js is loaded.
   *
   * @param {object} options
   * @param {object} options.THREE - Three.js module reference
   * @param {THREE.Scene} options.scene - the main scene
   * @param {THREE.Group} [options.toolGroup] - tool group for frustum attachment
   * @param {THREE.Group} [options.rootGroup] - root group for arm base axes
   * @param {THREE.Group[]} [options.modelGroups] - joint groups for TCP position
   * @returns {object} handles for runtime updates
   */
  function init(options) {
    THREE = options.THREE;
    const scene = options.scene;
    const params = new URLSearchParams(window.location.search);

    const handles = {
      worldAxes: null,
      armBaseAxes: null,
      tcpAxes: null,
      cameraMount: null,
      depthEnabled: false,
      coordsEnabled: false,
    };

    // ?axes or ?axes=all
    if (params.has("axes")) {
      const val = params.get("axes");

      // World origin axes (1m length)
      handles.worldAxes = createRosAxes(THREE, new THREE.Vector3(0, 0, 0), 1, "");
      scene.add(handles.worldAxes);
      createdObjects.push(handles.worldAxes);

      if (val === "all") {
        // Arm base axes (0.3m, placed at groups[0] origin)
        handles.armBaseAxes = createRosAxes(THREE, undefined, 0.3, "B:");
        if (options.rootGroup) {
          options.rootGroup.add(handles.armBaseAxes);
        } else {
          scene.add(handles.armBaseAxes);
        }
        createdObjects.push(handles.armBaseAxes);

        // TCP axes (0.15m, attached to toolGroup)
        handles.tcpAxes = createRosAxes(THREE, undefined, 0.15, "T:");
        if (options.toolGroup) {
          options.toolGroup.add(handles.tcpAxes);
        }
        createdObjects.push(handles.tcpAxes);
      }

      console.log("[scene-helpers] Axes enabled:", val || "world");
    }

    const wantsFrustum = params.has("camera_frustum");
    const wantsDepthCloud = params.has("depth_cloud");

    // Camera mount is required by both frustum and depth cloud
    if (wantsFrustum || wantsDepthCloud) {
      handles.cameraMount = createCameraMount(THREE, wantsFrustum);
      if (options.toolGroup) {
        options.toolGroup.add(handles.cameraMount);
      }
      createdObjects.push(handles.cameraMount);

      if (wantsDepthCloud) {
        const overlay = CFG.DEPTH_OVERLAY || {};
        const rec = overlay.recording || {};
        depthState.voxelSizeM = (Number(rec.voxelSizeMm) || 5) / 1000;
        depthState.maxVoxels = Number(rec.maxVoxels) || 400000;
        const live = ensureLiveDepthCloud();
        const map = ensureMapDepthCloud();
        handles.cameraMount.add(live);
        scene.add(map);
        depthState.enabled = true;
        depthState.recording = false;
        depthState.pendingShot = false;
        depthState.pendingShotColor = false;
        depthState.pendingClearFrame = false;
        depthState.panelEnabled = false;
        depthState.status = "paused";
        depthState.controlsEnabled = true;
        depthState.persistStatus = "";
        handles.depthEnabled = true;
        ensureCloudControlPanel();
        updateCloudControlPanel();
        updateDepthDebugPanel();
        if (!depthState.persistenceLoaded) {
          depthState.persistenceLoaded = true;
          loadPersistedMap();
        }
      }

      console.log("[scene-helpers] D435 camera mount enabled",
        "frustum=", wantsFrustum,
        "depth_cloud=", wantsDepthCloud,
        "transform:", CFG.TRANSFORMS.flangeToCamera);
    }

    // ?coords
    if (params.has("coords")) {
      handles.coordsEnabled = true;
      ensureCoordsHUD();
      console.log("[scene-helpers] Coords HUD enabled");
    }

    return handles;
  }


  /**
   * Cleanup all created helpers.
   */
  function dispose() {
    for (const obj of createdObjects) {
      if (obj.parent) obj.parent.remove(obj);
      obj.traverse(child => {
        if (child.geometry) child.geometry.dispose();
        if (child.material) {
          if (child.material.map) child.material.map.dispose();
          child.material.dispose();
        }
      });
    }
    createdObjects.length = 0;
    depthState.livePoints = null;
    depthState.liveGeometry = null;
    depthState.liveMaterial = null;
    depthState.mapPoints = null;
    depthState.mapGeometry = null;
    depthState.mapMaterial = null;
    depthState.voxelMap.clear();
    depthState.mapPointCount = 0;
    depthState.inFlight = false;
    depthState.enabled = false;
    depthState.recording = false;
    depthState.frameWidth = 0;
    depthState.frameHeight = 0;
    depthState.livePointsCount = 0;
    depthState.fps = 0;
    depthState.lastSuccessMs = 0;
    depthState.status = "init";
    depthState.panelEnabled = false;
    depthState.controlsEnabled = false;
    depthState.voxelSizeM = 0.005;
    depthState.maxVoxels = 400000;
    depthState.pendingShot = false;
    depthState.pendingShotColor = false;
    depthState.pendingClearFrame = false;
    depthState.persistStatus = "";
    depthState.persistenceLoaded = false;
    depthState.shotChunks.length = 0;
    depthState.shotSeq = 0;
    if (depthState.panelEl && depthState.panelEl.parentNode) {
      depthState.panelEl.parentNode.removeChild(depthState.panelEl);
    }
    depthState.panelEl = null;

    if (depthState.controlsEl && depthState.controlsEl.parentNode) {
      depthState.controlsEl.parentNode.removeChild(depthState.controlsEl);
    }
    depthState.controlsEl = null;
    cameraMountRef = null;
    if (coordsHudEl && coordsHudEl.parentNode) {
      coordsHudEl.parentNode.removeChild(coordsHudEl);
      coordsHudEl = null;
    }
  }


  // ═════════════════════════════════════════════════
  //  Export
  // ═════════════════════════════════════════════════
  window.SceneHelpers = Object.freeze({
    init,
    dispose,
    createRosAxes,
    createCameraMount,
    createFrustumGeometry,
    updateDepthOverlay,
    updateCoordsHUD,
    setDepthPointSize,
  });

  console.log("[scene-helpers] v20260222b loaded");
})();
