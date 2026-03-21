// arm3d-runtime.js — standalone Three.js viewer for xArm robots
// Version: 20260220ad
// Deps: scene-config.js, coord-utils.js, scene-helpers.js (loaded before this file)

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { STLLoader } from "three/addons/loaders/STLLoader.js";

(function () {
  // STL filename map: "axis-type" → [link0, link1, ...]
  const STL_MAP = {
    "5-5":  ["link0.a0b8702.stl","link1.65c358c.stl","link2.7567d0d.stl","link3.2a2a7bf.stl","link4.57d6fea.stl","link5.12b891c.stl"],
    "6-6":  ["link0.1de18b9.stl","link1.fc9e972.stl","link2.f35afe6.stl","link3.7c98c07.stl","link4.effe135.stl","link5.165f228.stl","link6.12b891c.stl"],
    "6-8":  ["link0.1de18b9.stl","link1.fc9e972.stl","link2.1ef6377.stl","link3.4a6fa41.stl","link4.6387be2.stl","link5.165f228.stl","link6.12b891c.stl"],
    "6-9":  ["link0.32eb957.stl","link1.96908fd.stl","link2.3e9c070.stl","link3.5e0ba78.stl","link4.bdad0a0.stl","link5.e57804e.stl","link6.28e8751.stl"],
    "6-11": ["link0.1de18b9.stl","link1.fc9e972.stl","link2.9a98a9d.stl","link3.ccdf24d.stl","link4.effe135.stl","link5.165f228.stl","link6.12b891c.stl"],
    "6-12": ["link0.f86babb.stl","link1.996b105.stl","link2.e8af0f9.stl","link3.be5eefa.stl","link4.d50a217.stl","link5.244fafb.stl","link6.cecc499.stl"],
    "7-7":  ["link0.d7f039f.stl","link1.bc742cf.stl","link2.eeae964.stl","link3.79cfa8a.stl","link4.2222f0d.stl","link5.bce977e.stl","link6.83ff53c.stl","link7.c30a9fc.stl"],
    "7-13": ["link0.def507c.stl","link1.f67a75b.stl","link2.06d3f89.stl","link3.43af14d.stl","link4.932ff1a.stl","link5.396c117.stl","link6.6ed2841.stl","link7.b47a9c7.stl"],
  };

  const D = Math.PI / 180;
  const z = v => D * v;
  const DEBUG_TRANSFORM_STORAGE_KEY = "arm3d.debug.modelTransform.delta.v2";
  let liftVisualNode = null;
  let baseGroupNode = null;

  // Hybrid scene references (set during init)
  const SC = window.SCENE_CONFIG || null;
  const CU = window.CoordUtils || null;
  let sceneHelperHandles = null;

  // ARM_SPECS: mirrors w object from main.b58ea3f.js
  //
  // Bundle initialises each group as:
  //   group.position = GROUPS_POSITION[i] * w.SCALE   (i.e. * [20,20,20])
  //
  // Bundle initialises each link mesh as:
  //   mesh.scale    = w.SCALE = [20,20,20]
  //   mesh.rotation = (MESHS_ROTATION[i] + w.ROTATION) deg → rad
  //                 = (MESHS_ROTATION[i] + [-90,0,-90]) deg → rad
  //
  // rootGroup is a plain container (no fixed rotation/scale/position).
  //
  // groupsPosition[i] — stored in the original "pre-scale" units (same as the bundle's GROUPS_POSITION).
  // meshsRotation[i]  — stored before adding w.ROTATION offset (same as MESHS_ROTATION).

  const ARM_SPECS = {
    "5-5": {
      groupsPosition: [
        [0,-0.25,0],[0,.267,0],[0,0,0],[0,.285,-.0535],[0,-.3425,-.0775],[0,-.097,-.076]
      ],
      meshsRotation: [
        [0,0,0],[0,0,180],[0,-90,180],[0,-90,180],[0,-90,180],[0,180,180]
      ],
      updateFn(g, M) {
        g[1].rotation.y =  z(M[0]-180);
        g[2].rotation.x = -z(M[1]);
        g[3].rotation.x = -z(M[2]);
        g[4].rotation.x = -z(M[3]);
        g[5].rotation.y = -z(M[4]);
      }
    },
    "6-6": {
      groupsPosition: [
        [0,-0.25,0],[0,.267,0],[0,0,0],[0,.285,-.0535],[0,-.3425,-.0775],[0,0,0],[0,-.097,-.076]
      ],
      meshsRotation: [
        [0,0,0],[0,0,180],[0,-90,180],[0,-90,180],[180,0,0],[0,-90,180],[0,180,180]
      ],
      updateFn(g, M, lift) {
        const newPos = ((lift||0)/10000)*1.55;
        if (liftVisualNode) {
          liftVisualNode.position.y = newPos;
          liftVisualNode.position.x = -newPos/3.5;
          liftVisualNode.position.z = -newPos/2;
        }
        g[1].position.y = newPos + 5;      // bundle line 353: (newPos)+5
        g[1].position.x = -(newPos/3.5);
        g[1].position.z = -(newPos/2);
        g[1].rotation.y =  z(M[0]-180);
        g[2].rotation.x = -z(M[1]);
        g[3].rotation.x = -z(M[2]);
        g[4].rotation.y = -z(M[3]);
        g[5].rotation.x = -z(M[4]);
        g[6].rotation.y = -z(M[5]);
      }
    },
    "6-8": {
      groupsPosition: [
        [0,-0.25,0],[0,.267,0],[0,0,0],[0,.418,-.0535],[0,-.4655,-.0775],[0,.002,0],[0,-.095,-.076]
      ],
      meshsRotation: [
        [0,0,0],[0,0,180],[0,-90,180],[0,90,180],[180,0,0],[0,-90,180],[0,180,180]
      ],
      updateFn(g, M) {
        g[1].rotation.y =  z(M[0]-180);
        g[2].rotation.x = -z(M[1]);
        g[3].rotation.x =  z(M[2]);
        g[4].rotation.y = -z(M[3]);
        g[5].rotation.x = -z(M[4]);
        g[6].rotation.y = -z(M[5]);
      }
    },
    "6-9": {
      groupsPosition: [
        [0,-0.25,0],[0,.2435,0],[0,0,0],[0,.2002,0],[0,-.22761,-.087],[0,0,0],[0,-.0625,0]
      ],
      meshsRotation: [
        [0,0,0],[0,0,180],[0,-90,90],[0,90,180],[180,0,0],[0,-90,180],[0,180,180]
      ],
      updateFn(g, M) {
        g[1].rotation.y =  z(M[0]-180);
        g[2].rotation.x = -z(M[1]);
        g[3].rotation.x =  z(M[2]);
        g[4].rotation.y = -z(M[3]);
        g[5].rotation.x = -z(M[4]);
        g[6].rotation.y = -z(M[5]);
      }
    },
    "6-11": {
      groupsPosition: [
        [0,-0.25,0],[0,.267,0],[0,0,0],[0,.445,-.0535],[0,-.3425,-.0775],[0,0,0],[0,-.097,-.076]
      ],
      meshsRotation: [
        [0,0,0],[0,0,180],[0,-90,180],[0,90,180],[180,0,0],[0,-90,180],[0,180,180]
      ],
      updateFn(g, M) {
        g[1].rotation.y =  z(M[0]-180);
        g[2].rotation.x = -z(M[1]);
        g[3].rotation.x =  z(M[2]);
        g[4].rotation.y =  z(M[3]);
        g[5].rotation.x = -z(M[4]);
        g[6].rotation.y = -z(M[5]);
      }
    },
    "6-12": {
      groupsPosition: [
        [0,-0.25,0],[0,.364,0],[0,0,0],[0,.39,0],[0,-.426,-.15],[0,0,0],[0,-.09,0]
      ],
      meshsRotation: [
        [0,0,0],[0,0,180],[180,-90,0],[0,-90,180],[0,180,180],[0,-90,180],[0,180,180]
      ],
      updateFn(g, M) {
        g[1].rotation.y =  z(M[0]-180);
        g[2].rotation.x =  z(M[1]);
        g[3].rotation.x = -z(M[2]);
        g[4].rotation.y = -z(M[3]);
        g[5].rotation.x =  z(M[4]);
        g[6].rotation.y = -z(M[5]);
      }
    },
    "7-7": {
      groupsPosition: [
        [0,-0.25,0],[0,.267,0],[0,0,0],[0,.293,0],[0,0,-.0525],[0,-.3425,-.0775],[0,0,0],[0,-.097,-.076]
      ],
      meshsRotation: [
        [0,0,0],[0,0,180],[0,-90,180],[0,0,180],[0,90,180],[180,0,0],[0,-90,180],[0,180,180]
      ],
      updateFn(g, M) {
        g[1].rotation.y =  z(M[0]-180);
        g[2].rotation.x = -z(M[1]);
        g[3].rotation.y =  z(M[2]);
        g[4].rotation.x =  z(M[3]);
        g[5].rotation.y = -z(M[4]);
        g[6].rotation.x = -z(M[5]);
        g[7].rotation.y = -z(M[6]);
      }
    },
    "7-13": {
      groupsPosition: [
        [0,-0.25,0],[0,.266,0],[0,0,0],[0,.292,0],[0,0,-.0525],[0,-.3425,-.0775],[0,0,0],[0,-.097,-.076]
      ],
      meshsRotation: [
        [0,0,0],[0,0,180],[0,90,180],[0,0,180],[0,-90,180],[180,0,0],[0,90,180],[0,180,180]
      ],
      updateFn(g, M) {
        g[1].rotation.y =  z(M[0]-180);
        g[2].rotation.x =  z(M[1]);
        g[3].rotation.y =  z(M[2]);
        g[4].rotation.x = -z(M[3]);
        g[5].rotation.y = -z(M[4]);
        g[6].rotation.x =  z(M[5]);
        g[7].rotation.y = -z(M[6]);
      }
    },
  };

  const SPEC_FALLBACK = { 5:"5-5", 6:"6-6", 7:"7-7" };
  function getSpec(axis, type) {
    return ARM_SPECS[`${axis}-${type}`] || ARM_SPECS[SPEC_FALLBACK[axis]] || ARM_SPECS["6-6"];
  }
  function getStlList(axis, type) {
    return STL_MAP[`${axis}-${type}`] || STL_MAP[SPEC_FALLBACK[axis]] || STL_MAP["6-6"];
  }

  const POLL_STATUS_INTERVAL_MS = 5000;

  const state = {
    axis: 6, type: 6,
    joints: [0,0,0,0,0,0],
    mountDegrees: [0,0],
    ignoreMount: false,
    debugRotation: [0,0,0],
    debugPosition: [0,0,0],
    showTransformPanel: false,
    lift: 0,
    liftTarget: 0,
    liftCurrent: 0,
    liftInitialized: false,
    mounted: false,
    frameReadySent: false,
    fallbackPollingEnabled: false,
    agvMapEnabled: false,
    symovoPose: null,
    parentOrigin: "*",
    useStl: true,
  };

  let renderer, scene, camera, controls;
  let rootGroup, modelGroups = [], toolGroup;
  let gridHelper;
  let mapLayerMesh = null;
  let mapLayerPivot = null;
  let mapLayerId = null;
  let mapLayerTopLeftRosX = 0;
  let mapLayerTopLeftRosY = 0;
  let mapLayerLoaded = false;
  let lastPoseMapMismatchKey = null;
  let robotAutoFocused = false;
  let stlLoader;
  let jointsPollTimer, statusPollTimer;
  let agvStatusPollTimer;
  let agvStatusInFlight = false;

  // ─── helpers ───
  function setStatus(t) {
    const el = document.getElementById("arm3d-status");
    if (el) el.textContent = t;
  }
  function degToRad(v) { return v * Math.PI / 180; }
  function safeF(v) { const n = parseFloat(v); return isFinite(n) ? n : 0; }
  function clampDeg(v) { return Math.max(-180, Math.min(180, safeF(v))); }
  function clampPos(v) { return Math.max(-20, Math.min(20, safeF(v))); }

  function normalizeAngleUnits(arr) {
    const nums = arr.map(safeF);
    const maxAbs = Math.max(...nums.map(Math.abs));
    if (maxAbs > 0 && maxAbs <= Math.PI*2 + 0.2)
      return nums.map(v => v * 180 / Math.PI);
    return nums;
  }

  function normalizeJoints(payload) {
    let arr;
    if (Array.isArray(payload)) arr = payload;
    else if (payload && Array.isArray(payload.joints)) arr = payload.joints;
    else if (payload && typeof payload === "object") {
      arr = [];
      for (let i=1; i<=8; i++) {
        const v = payload[`j${i}`];
        if (v !== undefined) arr.push(v);
      }
      if (!arr.length) arr = Object.values(payload).filter(v => typeof v === "number");
    } else arr = [];
    return normalizeAngleUnits(arr.map(safeF));
  }

  function normalizeMountDegrees(payload) {
    if (!Array.isArray(payload)) return [0,0];
    return [safeF(payload[0]), safeF(payload[1])];
  }

  function normalizeDebugRotation(payload) {
    if (!Array.isArray(payload)) return [0,0,0];
    return [
      clampDeg(payload[0]),
      clampDeg(payload[1]),
      clampDeg(payload[2]),
    ];
  }

  function normalizeDebugPosition(payload) {
    if (!Array.isArray(payload)) return [0,0,0];
    return [
      clampPos(payload[0]),
      clampPos(payload[1]),
      clampPos(payload[2]),
    ];
  }

  function loadDebugTransformFromStorage() {
    try {
      const raw = window.localStorage.getItem(DEBUG_TRANSFORM_STORAGE_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) {
        state.debugRotation = normalizeDebugRotation(parsed);
        state.debugPosition = [0,0,0];
        return;
      }
      state.debugRotation = normalizeDebugRotation(parsed.rotation);
      state.debugPosition = normalizeDebugPosition(parsed.position);
    } catch (_) {}
  }

  function saveDebugTransformToStorage() {
    try {
      window.localStorage.setItem(DEBUG_TRANSFORM_STORAGE_KEY, JSON.stringify({
        rotation: state.debugRotation,
        position: state.debugPosition,
      }));
    } catch (_) {}
  }

  function updateDebugControlsView() {
    const rotIds = ["x", "y", "z"];
    const posIds = ["x", "y", "z"];
    for (let i = 0; i < rotIds.length; i++) {
      const value = Math.round(state.debugRotation[i]);
      const input = document.getElementById(`arm3d-debug-r${rotIds[i]}`);
      const label = document.getElementById(`arm3d-debug-r${rotIds[i]}-value`);
      if (input) input.value = String(value);
      if (label) label.textContent = `${value}°`;
    }
    for (let i = 0; i < posIds.length; i++) {
      const value = Number(state.debugPosition[i]).toFixed(1);
      const input = document.getElementById(`arm3d-debug-p${posIds[i]}`);
      const label = document.getElementById(`arm3d-debug-p${posIds[i]}-value`);
      if (input) input.value = value;
      if (label) label.textContent = value;
    }
    const packed = document.getElementById("arm3d-debug-values");
    if (packed) {
      packed.innerHTML = `R ${Math.round(state.debugRotation[0])}/${Math.round(state.debugRotation[1])}/${Math.round(state.debugRotation[2])}<br>P ${state.debugPosition[0].toFixed(1)}/${state.debugPosition[1].toFixed(1)}/${state.debugPosition[2].toFixed(1)}`;
    }
  }

  function setupDebugRotationControls() {
    const panel = document.getElementById("arm3d-debug-controls");
    if (!panel) return;
    if (!state.showTransformPanel) {
      panel.style.display = "none";
      return;
    }
    panel.style.display = "block";

    const pauseControls = () => { if (controls) controls.enabled = false; };
    const resumeControls = () => { if (controls) controls.enabled = true; };
    panel.addEventListener("pointerdown", pauseControls);
    panel.addEventListener("pointerup", resumeControls);
    panel.addEventListener("pointercancel", resumeControls);
    panel.addEventListener("mouseleave", (event) => {
      if (!(event.buttons & 1)) resumeControls();
    });

    const axes = ["x", "y", "z"];
    for (let i = 0; i < axes.length; i++) {
      const input = document.getElementById(`arm3d-debug-r${axes[i]}`);
      if (!input) continue;
      input.addEventListener("input", () => {
        state.debugRotation[i] = clampDeg(input.value);
        updateDebugControlsView();
        saveDebugTransformToStorage();
        applyMountDegrees();
      });
    }

    for (let i = 0; i < axes.length; i++) {
      const input = document.getElementById(`arm3d-debug-p${axes[i]}`);
      if (!input) continue;
      input.addEventListener("input", () => {
        state.debugPosition[i] = clampPos(input.value);
        updateDebugControlsView();
        saveDebugTransformToStorage();
        applyMountDegrees();
      });
    }

    const reset = document.getElementById("arm3d-debug-reset");
    if (reset) {
      reset.addEventListener("click", () => {
        state.debugRotation = [0,0,0];
        state.debugPosition = [0,0,0];
        updateDebugControlsView();
        saveDebugTransformToStorage();
        applyMountDegrees();
      });
    }

    updateDebugControlsView();
  }

  function applyMountDegrees() {
    if (!modelGroups.length || !modelGroups[0]) return;
    const tilt = state.ignoreMount ? 0 : safeF(state.mountDegrees[0]);
    const rotate = state.ignoreMount ? -90 : (safeF(state.mountDegrees[1]) - 90);
    const canonical = SC?.TRANSFORMS?.armBaseCanonical || {};
    const canonicalPos = canonical.position_wu || {};
    const canonicalRot = canonical.rotation_deg || {};
    const debugPos = state.debugPosition;

    const posX = (tilt <= 90) ? (5 / 90) * tilt : (5 / 90) * (180 - tilt);
    const posY = (6 / 180) * tilt - 5;
    modelGroups[0].position.set(
      posX + (Number(canonicalPos.x) || 0) + debugPos[0],
      posY + (Number(canonicalPos.y) || 0) + debugPos[1],
      (Number(canonicalPos.z) || 0) + debugPos[2]
    );

    const axisZ = new THREE.Vector3(0, 0, 1).normalize();
    const axisMount = new THREE.Vector3(
      -Math.sin(degToRad(tilt)),
      Math.cos(degToRad(tilt)),
      0
    ).normalize();
    const qTilt = new THREE.Quaternion().setFromAxisAngle(axisZ, degToRad(tilt));
    const qRotate = new THREE.Quaternion().setFromAxisAngle(axisMount, degToRad(rotate));
    const qFinal = new THREE.Quaternion().multiplyQuaternions(qRotate, qTilt);
    const debugEuler = new THREE.Euler(
      degToRad((Number(canonicalRot.x) || 0) + state.debugRotation[0]),
      degToRad((Number(canonicalRot.y) || 0) + state.debugRotation[1]),
      degToRad((Number(canonicalRot.z) || 0) + state.debugRotation[2]),
      "XYZ"
    );
    const qDebug = new THREE.Quaternion().setFromEuler(debugEuler);
    qFinal.multiply(qDebug);
    modelGroups[0].setRotationFromQuaternion(qFinal);

    if (gridHelper) {
      gridHelper.position.set(0, -5, 0);
      gridHelper.rotation.set(0, 0, 0);
    }
  }

  // ─── postMessage ───
  function applyPostMessageState({type, payload}) {
    if (type === "arm3d:init") {
      const ax = parseInt(payload.axis)||state.axis;
      const ty = parseInt(payload.type)||state.type;
      const mount = normalizeMountDegrees(payload.mountDegrees);
      const changed = ax!==state.axis || ty!==state.type;
      const mountChanged = mount[0] !== state.mountDegrees[0] || mount[1] !== state.mountDegrees[1];
      state.axis=ax; state.type=ty;
      if (!state.ignoreMount) state.mountDegrees = mount;
      if (payload.joints) state.joints = normalizeJoints(payload.joints);
      if (changed) {
        rebuildArm();
      } else {
        if (!state.ignoreMount && mountChanged) applyMountDegrees();
        applyJointState();
      }
      setStatus(`arm3d: axis=${ax} type=${ty}`);
    } else if (type === "arm3d:state") {
      if (payload.joints) state.joints = normalizeJoints(payload.joints);
      if (!state.ignoreMount && payload.mountDegrees) {
        state.mountDegrees = normalizeMountDegrees(payload.mountDegrees);
        applyMountDegrees();
      }
      if (payload.lift !== undefined) {
        const nextLift = safeF(payload.lift);
        state.liftTarget = nextLift;
        if (!state.liftInitialized) {
          state.liftCurrent = nextLift;
          state.lift = nextLift;
          state.liftInitialized = true;
        }
      }
      applyJointState();
      disableFallbackPolling();
    } else if (type === "arm3d:config") {
      if (payload.fallbackPolling === false) disableFallbackPolling();
      if (payload && payload.ignoreMount !== undefined) {
        state.ignoreMount = !!payload.ignoreMount;
        applyMountDegrees();
      }
    }
  }

  function disableFallbackPolling() {
    state.fallbackPollingEnabled = false;
    if (jointsPollTimer) { clearInterval(jointsPollTimer); jointsPollTimer=null; }
    if (statusPollTimer) { clearInterval(statusPollTimer); statusPollTimer=null; }
  }

  function mapTemplate(pathTemplate, mapId) {
    return String(pathTemplate || "").replace("{map_id}", encodeURIComponent(String(mapId)));
  }

  function focusCameraOnRobotOnce() {
    if (robotAutoFocused || !camera || !controls || !rootGroup) return;
    const center = rootGroup.position.clone();
    const dir = camera.position.clone().sub(controls.target);
    if (dir.lengthSq() < 1e-6) dir.set(1, 0.7, 1);
    dir.normalize();
    const distance = Math.max(camera.position.distanceTo(controls.target), 30);
    camera.position.copy(center.clone().add(dir.multiplyScalar(distance)));
    controls.target.copy(center);
    controls.update();
    robotAutoFocused = true;
  }

  function applyAgvMapLayerTransform() {
    if (!mapLayerMesh || !mapLayerPivot || !SC || !CU) return;
    const cfg = SC?.AGV_VISUALIZATION?.mapLayer || {};

    // --- Texture: UV offset + rotation ---
    const tex = mapLayerMesh.material?.map;
    if (tex) {
      const widthM  = mapLayerMesh.userData.widthM  || 1;
      const heightM = mapLayerMesh.userData.heightM || 1;
      const uShift = Number(cfg.texOffsetXM ?? 0) / widthM;
      const vShift = Number(cfg.texOffsetYM ?? 0) / heightM;
      tex.offset.set(uShift, vShift);
      const texYawDeg = Number(cfg.textureRotationDeg ?? 0) + Number(cfg.texYawDeg ?? 0);
      tex.rotation = degToRad(texYawDeg);
      tex.center.set(0.5, 0.5);
      tex.needsUpdate = true;
    }

    // --- Plane position + rotation (relative origin = map top-left) ---
    const widthM = mapLayerMesh.userData.widthM || 1;
    const heightM = mapLayerMesh.userData.heightM || 1;
    const widthWu = CU.metersToWorld(widthM);
    const heightWu = CU.metersToWorld(heightM);
    // Apply same coordinate transforms as robot pose (swapXY, invertX, invertY)
    const poseCfg = SC?.AGV_VISUALIZATION?.pose || {};
    let mapX = mapLayerTopLeftRosX;
    let mapY = mapLayerTopLeftRosY;
    if (poseCfg.swapXY === true) { const tmp = mapX; mapX = mapY; mapY = tmp; }
    if (poseCfg.invertX === true) { mapX = -mapX; }
    if (poseCfg.invertY === true) { mapY = -mapY; }
    const [mx, _my, mz] = CU.rosMetersToThreeWorld(mapX, mapY, 0);

    // Map mesh local origin is center; move it so pivot origin equals top-left.
    mapLayerMesh.rotation.set(-Math.PI / 2, 0, 0);
    mapLayerMesh.position.set(widthWu / 2, 0, heightWu / 2);

    const planePosXM = Number(cfg.planePosXM ?? 0);
    const planePosYM = Number(cfg.planePosYM ?? 0);
    const planePosZM = Number(cfg.planePosZM ?? 0);
    const planeRx = degToRad(Number(cfg.planeRxDeg ?? 0));
    const planeRy = degToRad(Number(cfg.planeRyDeg ?? 0));
    const planeRz = degToRad(Number(cfg.planeRzDeg ?? 0));
    mapLayerPivot.rotation.set(planeRx, planeRy, planeRz);
    mapLayerPivot.position.set(
      mx + CU.metersToWorld(planePosXM),
      Number(cfg.yOffset_wu ?? -4.98) + CU.metersToWorld(planePosZM),
      mz - CU.metersToWorld(planePosYM)
    );
  }

  function setupAgvMapOffsetControls() {
    const panel = document.getElementById("arm3d-map-controls");
    if (!panel) return;
    if (!state.agvMapEnabled || !SC?.AGV_VISUALIZATION?.mapLayer) {
      panel.style.display = "none";
      return;
    }
    panel.style.display = "block";

    const cfg = SC.AGV_VISUALIZATION.mapLayer;
    // --- Plane position sliders ---
    const ppx  = document.getElementById("arm3d-map-plane-px");
    const ppy  = document.getElementById("arm3d-map-plane-py");
    const ppz  = document.getElementById("arm3d-map-plane-pz");
    const ppxV = document.getElementById("arm3d-map-plane-px-val");
    const ppyV = document.getElementById("arm3d-map-plane-py-val");
    const ppzV = document.getElementById("arm3d-map-plane-pz-val");
    // --- Plane rotation sliders ---
    const prx  = document.getElementById("arm3d-map-plane-rx");
    const pry  = document.getElementById("arm3d-map-plane-ry");
    const prz  = document.getElementById("arm3d-map-plane-rz");
    const prxV = document.getElementById("arm3d-map-plane-rx-val");
    const pryV = document.getElementById("arm3d-map-plane-ry-val");
    const przV = document.getElementById("arm3d-map-plane-rz-val");
    const packed = document.getElementById("arm3d-map-offset-values");
    const resetBtn = document.getElementById("arm3d-map-offset-reset");

    const pauseControls = () => { if (controls) controls.enabled = false; };
    const resumeControls = () => { if (controls) controls.enabled = true; };
    panel.addEventListener("pointerdown", pauseControls);
    panel.addEventListener("pointerup", resumeControls);
    panel.addEventListener("pointercancel", resumeControls);
    panel.addEventListener("mouseleave", (event) => {
      if (!(event.buttons & 1)) resumeControls();
    });

    const syncView = () => {
      const px = Number(cfg.planePosXM ?? 0), py = Number(cfg.planePosYM ?? 0), pz = Number(cfg.planePosZM ?? 0);
      const rx = Number(cfg.planeRxDeg ?? 0), ry = Number(cfg.planeRyDeg ?? 0), rz = Number(cfg.planeRzDeg ?? 0);
      if (ppx) ppx.value = px.toFixed(2); if (ppxV) ppxV.textContent = px.toFixed(2);
      if (ppy) ppy.value = py.toFixed(2); if (ppyV) ppyV.textContent = py.toFixed(2);
      if (ppz) ppz.value = pz.toFixed(2); if (ppzV) ppzV.textContent = pz.toFixed(2);
      if (prx) prx.value = rx; if (prxV) prxV.textContent = rx + "°";
      if (pry) pry.value = ry; if (pryV) pryV.textContent = ry + "°";
      if (prz) prz.value = rz; if (przV) przV.textContent = rz + "°";
      if (packed) packed.innerHTML = `Pos ${px.toFixed(2)}/${py.toFixed(2)}/${pz.toFixed(2)}<br>Rot ${rx}/${ry}/${rz}°`;
    };

    const bind = (el, key) => { if (el) el.addEventListener("input", () => { cfg[key] = Number(el.value) || 0; syncView(); applyAgvMapLayerTransform(); }); };
    bind(ppx, "planePosXM"); bind(ppy, "planePosYM"); bind(ppz, "planePosZM");
    bind(prx, "planeRxDeg"); bind(pry, "planeRyDeg"); bind(prz, "planeRzDeg");
    if (resetBtn) {
      resetBtn.addEventListener("click", () => {
        cfg.planePosXM = 0; cfg.planePosYM = 0; cfg.planePosZM = 0;
        cfg.planeRxDeg = 0; cfg.planeRyDeg = 0; cfg.planeRzDeg = 0;
        syncView();
        applyAgvMapLayerTransform();
      });
    }

    syncView();
  }

  function extractMapEntry(payload) {
    if (!payload) return null;
    if (Array.isArray(payload.result) && payload.result.length) return payload.result[0];
    if (Array.isArray(payload.data) && payload.data.length) return payload.data[0];
    if (Array.isArray(payload.maps) && payload.maps.length) return payload.maps[0];
    return payload.result && typeof payload.result === "object" ? payload.result : null;
  }

  async function fetchMapMetadata() {
    const cfg = SC?.AGV_VISUALIZATION?.mapLayer;
    if (!cfg) return null;

    const listUrl = cfg.mapListUrl || "/api/v1/symovo/map";
    const listResp = await fetch(listUrl, { cache: "no-store" });
    if (!listResp.ok) throw new Error(`Map list HTTP ${listResp.status}`);
    const listPayload = await listResp.json();

    if (cfg.preferredMapId != null) {
      const prefId = Number(cfg.preferredMapId);
      const resultArr = Array.isArray(listPayload?.result) ? listPayload.result : [];
      const preferred = resultArr.find((item) => Number(item?.id) === prefId);
      if (preferred) return preferred;
      const metaUrl = mapTemplate(cfg.mapMetaUrlTemplate || "/api/v1/symovo/map/{map_id}", prefId);
      const metaResp = await fetch(metaUrl, { cache: "no-store" });
      if (metaResp.ok) {
        const metaPayload = await metaResp.json();
        const entry = extractMapEntry(metaPayload);
        if (entry) return entry;
      }
    }

    return extractMapEntry(listPayload);
  }

  async function loadAgvMapLayerOnce() {
    if (!state.agvMapEnabled || mapLayerLoaded || !scene || !CU || !SC) return;
    const cfg = SC.AGV_VISUALIZATION?.mapLayer;
    if (!cfg) return;

    try {
      const entry = await fetchMapMetadata();
      if (!entry) return;

      const size = Array.isArray(entry.size) ? entry.size : null;
      const widthPx = size ? Number(size[0]) : Number(entry.width);
      const heightPx = size ? Number(size[1]) : Number(entry.height);
      const resolution = Number(entry.resolution);
      const offsetX = Number(entry.offsetX ?? entry.offset_x ?? 0);
      const offsetY = Number(entry.offsetY ?? entry.offset_y ?? 0);
      const mapId = Number(entry.id ?? entry.map_id ?? 0);
      if (!isFinite(widthPx) || !isFinite(heightPx) || !isFinite(resolution) || widthPx <= 0 || heightPx <= 0 || resolution <= 0 || !isFinite(mapId)) {
        return;
      }
      mapLayerId = mapId;

      const imageUrl = mapTemplate(cfg.mapImageUrlTemplate || "/api/v1/symovo/map/{map_id}/full.png", mapId);
      const texture = await new Promise((resolve, reject) => {
        new THREE.TextureLoader().load(imageUrl, resolve, undefined, reject);
      });
      texture.colorSpace = THREE.SRGBColorSpace;
      texture.needsUpdate = true;

      const widthM = widthPx * resolution;
      const heightM = heightPx * resolution;
      // Top-left origin in map world coordinates (same convention as map_viewer):
      // x = offsetX, y = offsetY + mapHeight.
      mapLayerTopLeftRosX = offsetX;
      mapLayerTopLeftRosY = offsetY + heightM;

      const plane = new THREE.Mesh(
        new THREE.PlaneGeometry(CU.metersToWorld(widthM), CU.metersToWorld(heightM)),
        new THREE.MeshBasicMaterial({
          map: texture,
          transparent: true,
          opacity: Number(cfg.opacity ?? 0.8),
          side: THREE.DoubleSide,
          depthWrite: false,
        })
      );
      const pivot = new THREE.Group();
      pivot.name = "agv_map_pivot";
      plane.name = "agv_map_layer";
      plane.renderOrder = -2;
      plane.userData.widthM = widthM;
      plane.userData.heightM = heightM;
      pivot.add(plane);
      scene.add(pivot);
      mapLayerMesh = plane;
      mapLayerPivot = pivot;
      robotAutoFocused = false;
      console.log("[arm3d] AGV map top-left origin (ros m)", {
        mapId: mapLayerId,
        x: mapLayerTopLeftRosX,
        y: mapLayerTopLeftRosY,
        offsetX,
        offsetY,
        widthM,
        heightM,
      });
      applyAgvMapLayerTransform();
      mapLayerLoaded = true;
    } catch (e) {
      console.warn("[arm3d] AGV map load failed:", e?.message || e);
    }
  }

  function applyRobotPoseFromUnifiedStatus(payload) {
    if (!payload || !payload.symovo || !payload.symovo.pose || !rootGroup || !CU) return;
    const pose = payload.symovo.pose;
    const poseMapId = Number(pose.map_id);
    if (
      Number.isFinite(mapLayerId) &&
      mapLayerId > 0 &&
      Number.isFinite(poseMapId) &&
      poseMapId > 0 &&
      poseMapId !== mapLayerId
    ) {
      const mismatchKey = `${mapLayerId}:${poseMapId}`;
      if (lastPoseMapMismatchKey !== mismatchKey) {
        lastPoseMapMismatchKey = mismatchKey;
        console.warn("[arm3d] pose map_id mismatch, skip pose", {
          mapLayerId,
          poseMapId,
        });
      }
      return;
    }
    lastPoseMapMismatchKey = null;
    const xM = Number(pose.x_m);
    const yM = Number(pose.y_m);
    const thetaDeg = Number(pose.theta_deg);
    if (!isFinite(xM) || !isFinite(yM) || !isFinite(thetaDeg)) return;
    const poseCfg = SC?.AGV_VISUALIZATION?.pose || {};
    let poseX = xM;
    let poseY = yM;
    let poseThetaDeg = thetaDeg;

    if (poseCfg.swapXY === true) {
      const prevX = poseX;
      poseX = poseY;
      poseY = prevX;
    }
    if (poseCfg.invertX === true) {
      poseX = -poseX;
      poseThetaDeg = 180 - poseThetaDeg;
    }
    if (poseCfg.invertY === true) {
      poseY = -poseY;
    }
    if (poseCfg.invertYaw === true) {
      poseThetaDeg = -poseThetaDeg;
    }

    state.symovoPose = {
      xM: poseX,
      yM: poseY,
      thetaDeg: poseThetaDeg,
      mapId: poseMapId,
    };

    const anchorMode = String(SC?.AGV_VISUALIZATION?.pose?.anchorMode || "agv_center").toLowerCase();
    const agvToArm = SC?.TRANSFORMS?.agvToArmBase || {};
    const yawRad = degToRad(poseThetaDeg);

    let armX = poseX;
    let armY = poseY;
    let armZ = 0;
    let rollDeg = 0;
    let pitchDeg = 0;
    let yawDeg = poseThetaDeg;

    if (anchorMode === "arm_base") {
      const tx = Number(agvToArm.tx) || 0;
      const ty = Number(agvToArm.ty) || 0;
      const tz = Number(agvToArm.tz) || 0;
      armX = xM + tx * Math.cos(yawRad) - ty * Math.sin(yawRad);
      armY = yM + tx * Math.sin(yawRad) + ty * Math.cos(yawRad);
      armZ = tz;
      rollDeg += Number(agvToArm.rx) || 0;
      pitchDeg += Number(agvToArm.ry) || 0;
      yawDeg += Number(agvToArm.rz) || 0;
    }

    const [px, py, pz] = CU.rosMetersToThreeWorld(armX, armY, armZ);
    rootGroup.position.set(px, py, pz);
    focusCameraOnRobotOnce();

    const yawOffsetDeg = Number(SC?.AGV_VISUALIZATION?.pose?.yawOffsetDeg) || 0;
    const yawRosDeg = yawDeg + yawOffsetDeg;
    const rollRosRad = degToRad(rollDeg);
    const pitchRosRad = degToRad(pitchDeg);
    const yawRosRad = degToRad(yawRosDeg);
    const [rx, ry, rz] = CU.rosRPYToThree(rollRosRad, pitchRosRad, yawRosRad);
    rootGroup.rotation.set(rx, ry, rz, "XYZ");
  }

  function pickJointArrayFromXarmData(dataArray) {
    if (!Array.isArray(dataArray)) return null;

    const direct = dataArray[19];
    if (Array.isArray(direct) && direct.length >= 6) {
      return direct;
    }

    const alternate = dataArray[18];
    if (Array.isArray(alternate) && alternate.length >= 6) {
      return alternate;
    }

    for (const entry of dataArray) {
      if (!Array.isArray(entry) || entry.length < 6 || entry.length > 8) continue;
      const nums = entry.map((v) => Number(v));
      if (nums.some((v) => !Number.isFinite(v))) continue;
      if (nums.some((v) => Math.abs(v) > 720)) continue;
      if (nums.every((v) => Math.abs(v) < 1e-6)) continue;
      return nums;
    }

    return null;
  }

  function applyArmStateFromUnifiedStatus(payload) {
    if (!payload) return;
    const xarm = payload.xarm || {};
    const summary = xarm.summary || {};
    const xarmDataJoints = pickJointArrayFromXarmData(xarm.data);

    const jointsRaw =
      payload.joints ||
      xarm.joints ||
      summary.joints ||
      xarm.angles ||
      summary.angles ||
      xarmDataJoints ||
      xarm.datas ||
      summary.datas ||
      null;
    if (jointsRaw) {
      state.joints = normalizeJoints(Array.isArray(jointsRaw) ? jointsRaw : (jointsRaw.joints || jointsRaw));
    }

    const axisCandidate = Number(
      payload.xarm_axis ??
      summary.xarm_axis ??
      xarm.xarm_axis
    );
    const typeCandidate = Number(
      payload.xarm_device_type ??
      summary.xarm_device_type ??
      xarm.xarm_device_type
    );
    if (Number.isFinite(axisCandidate) && Number.isFinite(typeCandidate)) {
      const axis = [5, 6, 7].includes(axisCandidate) ? axisCandidate : state.axis;
      const type = Number.isFinite(typeCandidate) ? typeCandidate : state.type;
      if (axis !== state.axis || type !== state.type) {
        state.axis = axis;
        state.type = type;
        rebuildArm();
      }
    }

    const igus = payload.igus || {};
    const liftCandidate = igus.position;
    if (liftCandidate !== undefined && liftCandidate !== null) {
      const nextLift = safeF(liftCandidate);
      state.liftTarget = nextLift;
      if (!state.liftInitialized) {
        state.liftCurrent = nextLift;
        state.lift = nextLift;
        state.liftInitialized = true;
      }
    }

    applyJointState();
  }

  async function pollUnifiedRobotStatus() {
    if (!state.agvMapEnabled || agvStatusInFlight) return;
    const statusUrl = SC?.AGV_VISUALIZATION?.robotStatus?.url || "/api/v1/robot/status";
    agvStatusInFlight = true;
    try {
      const r = await fetch(statusUrl, { cache: "no-store" });
      if (!r.ok) return;
      const payload = await r.json();
      applyArmStateFromUnifiedStatus(payload);
      applyRobotPoseFromUnifiedStatus(payload);
    } catch (_) {
      // Keep render loop alive; silent in polling mode.
    } finally {
      agvStatusInFlight = false;
    }
  }

  function startUnifiedRobotStatusPolling() {
    if (!state.agvMapEnabled) return;
    if (agvStatusPollTimer) {
      clearInterval(agvStatusPollTimer);
      agvStatusPollTimer = null;
    }
    const intervalMs = Math.max(100, Number(SC?.AGV_VISUALIZATION?.robotStatus?.pollMs) || 500);
    pollUnifiedRobotStatus();
    agvStatusPollTimer = setInterval(pollUnifiedRobotStatus, intervalMs);
  }

  function sendToParent(type, payload) {
    try { window.parent.postMessage({type,payload}, state.parentOrigin); } catch(_){}
  }

  // ─── Three.js scene ───
  function createRenderer() {
    const el = document.getElementById("viewer");
    renderer = new THREE.WebGLRenderer({antialias:true, alpha:true});
    renderer.setPixelRatio(Math.min(window.devicePixelRatio,2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.setSize(el.clientWidth, el.clientHeight);
    el.appendChild(renderer.domElement);
  }

  function createScene() {
    const el = document.getElementById("viewer");
    scene = new THREE.Scene();

    // Use config for background, fallback to hardcoded
    const bgColor = SC ? SC.SCENE.background : "#0f1116";
    scene.background = new THREE.Color(bgColor);

    // Camera: view from front-right, arm stands along +Y.
    const camCfg = SC ? SC.SCENE_CAMERA : null;
    camera = new THREE.PerspectiveCamera(
      camCfg ? camCfg.fov : 45,
      el.clientWidth / el.clientHeight,
      camCfg ? camCfg.near : 0.1,
      camCfg ? camCfg.far : 500
    );
    const camPos = camCfg ? camCfg.initialPosition : [20, 15, 20];
    camera.position.set(camPos[0], camPos[1], camPos[2]);

    controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = camCfg ? camCfg.controls.enableDamping : true;
    controls.dampingFactor = camCfg ? camCfg.controls.dampingFactor : 0.08;
    const camTarget = camCfg ? camCfg.initialTarget : [0, 5, 0];
    controls.target.set(camTarget[0], camTarget[1], camTarget[2]);
    controls.minDistance = camCfg ? camCfg.controls.minDistance : 5;
    controls.maxDistance = camCfg ? camCfg.controls.maxDistance : 200;
    if (state.agvMapEnabled) {
      controls.maxDistance = Math.max(controls.maxDistance, 2000);
      camera.far = Math.max(camera.far, 5000);
      camera.updateProjectionMatrix();
    }
    controls.update();

    // Lights — from config or hardcoded
    const lightsCfg = SC ? SC.LIGHTS : null;
    if (lightsCfg) {
      for (const lc of lightsCfg) {
        if (lc.type === "ambient") {
          scene.add(new THREE.AmbientLight(lc.color, lc.intensity));
        } else if (lc.type === "directional") {
          const dl = new THREE.DirectionalLight(lc.color, lc.intensity);
          dl.position.set(lc.position[0], lc.position[1], lc.position[2]);
          if (lc.castShadow) dl.castShadow = true;
          scene.add(dl);
        }
      }
    } else {
      scene.add(new THREE.AmbientLight(0xffffff, 0.5));
      const key = new THREE.DirectionalLight(0xffffff, 0.9);
      key.position.set(20, 40, 30); key.castShadow = true;
      scene.add(key);
      const fill = new THREE.DirectionalLight(0x8090ff, 0.3);
      fill.position.set(-30, 20, -30);
      scene.add(fill);
    }

    // Grid / floor
    const floorCfg = SC ? SC.FLOOR : null;
    gridHelper = new THREE.GridHelper(
      floorCfg ? floorCfg.gridSize : 80,
      floorCfg ? floorCfg.gridDivisions : 24,
      floorCfg ? floorCfg.gridColors[0] : 0x303846,
      floorCfg ? floorCfg.gridColors[1] : 0x232a36
    );
    gridHelper.position.y = floorCfg ? floorCfg.positionY : -5;
    scene.add(gridHelper);

    // rootGroup: plain container to match bundle init chain.
    rootGroup = new THREE.Group();
    scene.add(rootGroup);

    toolGroup = new THREE.Group();
  }

  // materials
  const MAT_LINK = () => new THREE.MeshPhongMaterial({color:0xffffff, specular:0x333333, shininess:40});
  const MAT_BASE = () => new THREE.MeshPhongMaterial({color:0xcccccc, specular:0x222222, shininess:30});
  const MAT_TOOL = () => new THREE.MeshPhongMaterial({color:0xf5f5f5, specular:0x111111, shininess:20});

  // ─── build arm ───
  function buildArmFromSpec() {
    while (rootGroup.children.length) rootGroup.remove(rootGroup.children[0]);
    modelGroups = [];
    liftVisualNode = null;
    baseGroupNode = null;

    const spec = getSpec(state.axis, state.type);
    const stlList = getStlList(state.axis, state.type);
    const n = spec.groupsPosition.length;

    for (let i=0; i<n; i++) {
      const g = new THREE.Group();
      const p = spec.groupsPosition[i];
      // Bundle line 795: group.position = GROUPS_POSITION[i] * w.SCALE (= *20)
      g.position.set(p[0] * 20, p[1] * 20, p[2] * 20);
      modelGroups.push(g);
    }

    // chain groups: groups[i] is child of groups[i-1]
    rootGroup.add(modelGroups[0]);
    for (let i=1; i<n; i++) modelGroups[i-1].add(modelGroups[i]);

    // tool at tip
    modelGroups[n-1].add(toolGroup);
    while (toolGroup.children.length) toolGroup.remove(toolGroup.children[0]);

    // fallback joint spheres while STLs load (radius 2 = visible in scale-20 world)
    for (let i=0; i<n; i++) {
      const sg = new THREE.SphereGeometry(2, 8, 6);
      const sm = new THREE.MeshPhongMaterial({color: i===0 ? 0x8792a2 : 0x4b5563});
      const sphere = new THREE.Mesh(sg, sm);
      sphere.name = `jball_${i}`;
      modelGroups[i].add(sphere);
      if (i === 0) liftVisualNode = sphere;
    }

    // load STLs
    if (state.useStl && stlLoader) {
      for (let i=0; i<n; i++) {
        if (stlList[i]) loadLinkStl(i, stlList[i], spec.meshsRotation[i]);
      }
      loadBaseStl();
      loadToolStl();
    }

    applyMountDegrees();
    applyJointState();
  }

  function loadLinkStl(idx, fname, rot) {
    // Bundle:
    //   mesh.scale    = w.SCALE = [20,20,20]
    //   mesh.rotation = (MESHS_ROTATION + w.ROTATION) deg → rad
    //                 = (rot + [-90, 0, -90]) deg → rad
    stlLoader.load(`/static/stl/${fname}`, (geo) => {
      geo.computeVertexNormals();
      const mesh = new THREE.Mesh(geo, MAT_LINK());
      mesh.scale.set(20, 20, 20);
      const rx = (rot ? rot[0] : 0) - 90;
      const ry = (rot ? rot[1] : 0) + 0;
      const rz = (rot ? rot[2] : 0) - 90;
      mesh.rotation.set(degToRad(rx), degToRad(ry), degToRad(rz), "XYZ");
      mesh.castShadow = mesh.receiveShadow = true;
      const old = modelGroups[idx]?.getObjectByName(`jball_${idx}`);
      if (old) modelGroups[idx].remove(old);
      modelGroups[idx]?.add(mesh);
      if (idx === 0) liftVisualNode = mesh;
    }, undefined, (err) => console.warn(`[arm3d] link${idx} STL error:`, err));
  }

  function loadBaseStl() {
    // BASE: GROUP_POSITION=[-0.01,0.05,-0.02] * w.SCALE = [-0.2,1.0,-0.4]
    // mesh.scale=[20,20,20], mesh.rotation=[0,0,0]+[-90,0,-90]=[-90,0,-90]
    const baseGroup = new THREE.Group();
    baseGroup.position.set(-0.01 * 20, 0.05 * 20, -0.02 * 20);
    baseGroup.userData.basePos = {
      x: baseGroup.position.x,
      y: baseGroup.position.y,
      z: baseGroup.position.z,
    };
    baseGroupNode = baseGroup;
    (modelGroups[0] || rootGroup).add(baseGroup);
    stlLoader.load("/static/stl/base.stl", (geo) => {
      geo.computeVertexNormals();
      const mesh = new THREE.Mesh(geo, MAT_BASE());
      mesh.scale.set(20, 20, 20);
      mesh.rotation.set(degToRad(-90), 0, degToRad(-90), "XYZ");
      mesh.castShadow = mesh.receiveShadow = true;
      baseGroup.add(mesh);
    }, undefined, (err) => console.warn("[arm3d] base.stl error:", err));
  }

  function loadToolStl() {
    // xarm_vacuum_gripper: SCALE=[.001,.001,.001], MESHS_ROTATION=[180,0,0]
    // mesh.scale = w.SCALE * SCALE = [20*.001] = [0.02,0.02,0.02]
    // mesh.rotation = [180,0,0]+[-90,0,-90] = [90,0,-90] deg
    stlLoader.load("/static/stl/xarm_vacuum_gripper.2f2a316.stl", (geo) => {
      geo.computeVertexNormals();
      const mesh = new THREE.Mesh(geo, MAT_TOOL());
      mesh.scale.set(0.02, 0.02, 0.02);
      mesh.rotation.set(degToRad(90), 0, degToRad(-90), "XYZ");
      mesh.castShadow = true;
      toolGroup.add(mesh);
    }, undefined, (err) => {
      console.warn("[arm3d] tool STL error:", err);
      const cone = new THREE.Mesh(
        new THREE.ConeGeometry(1.1, 4.5, 12),
        new THREE.MeshPhongMaterial({color:0xf59e0b})
      );
      cone.rotation.x = Math.PI/2;
      toolGroup.add(cone);
    });
  }

  function rebuildArm() { buildArmFromSpec(); }

  // ─── apply joints ───
  function applyJointState() {
    if (!modelGroups.length) return;
    const spec = getSpec(state.axis, state.type);
    // reset all joint rotations
    for (let i=1; i<modelGroups.length; i++) modelGroups[i].rotation.set(0,0,0);
    // reset groups[1] position to spec default (×20) before lift offset
    const p1 = spec.groupsPosition[1];
    if (modelGroups[1]) modelGroups[1].position.set(p1[0] * 20, p1[1] * 20, p1[2] * 20);
    if (baseGroupNode && baseGroupNode.userData?.basePos) {
      baseGroupNode.position.set(
        baseGroupNode.userData.basePos.x,
        baseGroupNode.userData.basePos.y,
        baseGroupNode.userData.basePos.z
      );
    }
    try {
      spec.updateFn(modelGroups, state.joints, state.lift);
    } catch(e) { console.warn("[arm3d] updateFn error:", e); }
  }

  // ─── resize ───
  function onResize() {
    const el = document.getElementById("viewer");
    if (!el||!camera||!renderer) return;
    camera.aspect = el.clientWidth/el.clientHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(el.clientWidth, el.clientHeight);
  }

  // ─── render loop ───
  function animationLoop() {
    requestAnimationFrame(animationLoop);
    if (state.liftInitialized) {
      const delta = state.liftTarget - state.liftCurrent;
      if (Math.abs(delta) > 0.02) {
        state.liftCurrent += delta * 0.25;
        if (Math.abs(state.liftTarget - state.liftCurrent) < 0.02) {
          state.liftCurrent = state.liftTarget;
        }
        state.lift = state.liftCurrent;
        applyJointState();
      }
    }
    // Coords HUD: show TCP world position if enabled
    if (sceneHelperHandles && sceneHelperHandles.coordsEnabled && toolGroup) {
      const wp = new THREE.Vector3();
      toolGroup.getWorldPosition(wp);
      const rootPose = rootGroup
        ? {
            xWu: rootGroup.position.x,
            yWu: rootGroup.position.y,
            zWu: rootGroup.position.z,
            yawYDeg: THREE.MathUtils.radToDeg(rootGroup.rotation.y),
          }
        : null;
      window.SceneHelpers.updateCoordsHUD(wp, state.joints, state.lift, state.symovoPose, rootPose);
    }
    if (sceneHelperHandles && sceneHelperHandles.depthEnabled) {
      window.SceneHelpers.updateDepthOverlay(performance.now());
    }
    controls.update();
    renderer.render(scene, camera);
  }

  // ─── API fallback polling ───
  async function pollApiFallbackStatus() {
    if (!state.fallbackPollingEnabled) return;
    try {
      const r = await fetch("/api/v1/xarm/status");
      if (!r.ok) return;
      const data = await r.json();
      const ax = parseInt(data.axis)||state.axis;
      const ty = parseInt(data.device_type)||state.type;
      const jointsRaw =
        data.joints ||
        data.angles ||
        data.datas ||
        (data.state && (data.state.joints || data.state.angles)) ||
        null;
      if (jointsRaw) {
        state.joints = normalizeJoints(Array.isArray(jointsRaw) ? jointsRaw : (jointsRaw.joints || jointsRaw));
      }

      const liftCandidate =
        data.lift ??
        data.lift_position ??
        data.liftHeight ??
        data.igus_position ??
        (data.igus && data.igus.position);
      if (liftCandidate !== undefined && liftCandidate !== null) {
        const nextLift = safeF(liftCandidate);
        state.liftTarget = nextLift;
        if (!state.liftInitialized) {
          state.liftCurrent = nextLift;
          state.lift = nextLift;
          state.liftInitialized = true;
        }
      }

      if (ax!==state.axis || ty!==state.type) {
        state.axis=ax; state.type=ty;
        rebuildArm();
      } else {
        applyJointState();
      }
      setStatus("arm3d: api-fallback(status)");
    } catch(_){}
  }

  // ─── message channel ───
  function setupMessageChannel() {
    window.addEventListener("message", (event) => {
      if (!event.data || typeof event.data.type !== "string") return;
      if (event.origin && event.origin !== "null") state.parentOrigin = event.origin;
      applyPostMessageState(event.data);
    });
    // announce ready to parent
    setTimeout(() => {
      if (!state.frameReadySent) {
        state.frameReadySent = true;
        sendToParent("arm3d:ready", {version:1, capabilities:["init","state","config","api-fallback"]});
        sendToParent("arm3d:requestSnapshot", {});
      }
    }, 150);
  }

  // ─── query config ───
  function parseQueryConfig() {
    const p = new URLSearchParams(window.location.search);
    if (p.get("fallback")==="1") state.fallbackPollingEnabled = true;
    if (p.get("stl")==="0") state.useStl = false;
    if (p.get("ignore_mount")==="1") state.ignoreMount = true;
    state.agvMapEnabled = !!(SC?.AGV_VISUALIZATION?.enabled);
    if (p.has("agv_map")) {
      state.agvMapEnabled = p.get("agv_map") !== "0";
    }
    if (p.get("axis")) state.axis = parseInt(p.get("axis"))||6;
    if (p.get("type")) state.type = parseInt(p.get("type"))||6;
    state.showTransformPanel = p.has("transform");
    if (state.showTransformPanel && (p.get("dbg_rx") !== null || p.get("dbg_ry") !== null || p.get("dbg_rz") !== null)) {
      state.debugRotation = normalizeDebugRotation([
        p.get("dbg_rx"),
        p.get("dbg_ry"),
        p.get("dbg_rz"),
      ]);
    }
    if (state.showTransformPanel && (p.get("dbg_px") !== null || p.get("dbg_py") !== null || p.get("dbg_pz") !== null)) {
      state.debugPosition = normalizeDebugPosition([
        p.get("dbg_px"),
        p.get("dbg_py"),
        p.get("dbg_pz"),
      ]);
    }
  }

  // ─── init ───
  function init() {
    parseQueryConfig();
    loadDebugTransformFromStorage();
    stlLoader = new STLLoader();
    createRenderer();
    createScene();
    loadAgvMapLayerOnce();
    setupAgvMapOffsetControls();
    setupDebugRotationControls();
    rebuildArm();

    // Initialize scene helpers (axes, frustum, HUD) if available
    if (window.SceneHelpers) {
      sceneHelperHandles = window.SceneHelpers.init({
        THREE,
        scene,
        toolGroup,
        rootGroup,
        modelGroups,
      });
    }
    setupMessageChannel();

    window.addEventListener("resize", onResize);
    new ResizeObserver(onResize).observe(document.getElementById("viewer"));

    animationLoop();

    startUnifiedRobotStatusPolling();

    if (state.fallbackPollingEnabled) {
      pollApiFallbackStatus();
      statusPollTimer = setInterval(pollApiFallbackStatus, POLL_STATUS_INTERVAL_MS);
    }

    state.mounted = true;
    const ver = "20260220ad";
    const helperStatus = sceneHelperHandles ? "helpers=on" : "helpers=off";
    setStatus(`arm3d: ready v${ver} (stl=${state.useStl?"on":"off"}, ${helperStatus}, agv_map=${state.agvMapEnabled?"on":"off"})`);
  }

  init();
})();
