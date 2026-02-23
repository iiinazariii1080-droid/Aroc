/**
 * scene-config.ts — Deep-frozen, declarative scene configuration.
 *
 * Single source of truth for all constants, intrinsics, transforms, and
 * rendering parameters. Migrated from legacy scene-config.js with full
 * type safety and deep-freeze.
 *
 * RULE: This file is READONLY at runtime. No mutations.
 * Runtime state lives in the EventBus / domain modules.
 */

import type {
  StaticTransform,
  CameraIntrinsics,
  DepthCalibration,
  DepthOverlayConfig,
  VoxelRecordingConfig,
  PointCloudRenderConfig,
} from '@/types';

// ─────────────────────────────────────────────────
// §1  WORLD — global coordinate conventions
// ─────────────────────────────────────────────────

export const WORLD = {
  convention: 'Z-up' as const,
  units: 'meters' as const,
  coordinateSystem: 'ROS' as const,

  /** Legacy scale: 1 meter = 20 Three.js world units. */
  SCALE_FACTOR: 20,

  /** 1 world unit = 50 mm. */
  MM_PER_WU: 50,

  /** Alias for SCALE_FACTOR. */
  WU_PER_METER: 20,
} as const;


// ─────────────────────────────────────────────────
// §2  STATIC TRANSFORMS
// ─────────────────────────────────────────────────

export const TRANSFORMS: Record<string, StaticTransform> = {
  /**
   * Canonical arm-base visual transform in ROS frame.
   * Used as the zero reference for debug deltas.
   * Stored in world units / degrees for backward compat with rendering layer.
   */
  armBaseCanonical: {
    translation: { x: 0, y: -0.1, z: 0.525 },  // m (was wu: 0, -2, 10.5)
    rotation: { roll: 0, pitch: 0, yaw: 0 },
    status: 'KNOWN',
    note: 'Canonical zero reference (visual only). Legacy wu: {x:0, y:10.5, z:-2}, deg: {x:30, y:-30, z:0}',
  },

  /** T_agv → arm_base. Source: physical mounting drawings. */
  agvToArmBase: {
    translation: { x: 0, y: 0, z: 0 },
    rotation: { roll: 0, pitch: 0, yaw: 0 },
    status: 'PLACEHOLDER',
    note: 'USER MUST FILL from mounting CAD',
  },

  /**
   * T_flange → D435 camera.
   * D435 mounted on the SIDE of the vacuum gripper body,
   * pointing sideways (perpendicular to suction axis).
   *
   * Position: 44mm along local X, -168mm along local Y (toward tip), -27mm along local Z.
   * Rotation: rx=-90° ry=0° rz=-90° — rotates camera optical axis to match physical mount.
   */
  flangeToCamera: {
    translation: { x: 0.044, y: -0.168, z: -0.027 },
    rotation: { roll: -Math.PI / 2, pitch: 0, yaw: -Math.PI / 2 },
    status: 'KNOWN',
    note: 'Calibrated 2026-02-20. P:44/-168/-27mm R:-90/0/-90 deg (in toolGroup local frame)',
  },

  /** T_flange → gripper. Zero offset (gripper STL at tool group origin). */
  flangeToGripper: {
    translation: { x: 0, y: 0, z: 0 },
    rotation: { roll: 0, pitch: 0, yaw: 0 },
    status: 'KNOWN',
  },

  /** Base plate visual offset from groups[0]. */
  basePlate: {
    translation: { x: -0.01, y: -0.02, z: 0.05 },  // ROS meters (was pre-scale xyz)
    rotation: { roll: 0, pitch: 0, yaw: 0 },
    status: 'KNOWN',
    note: 'Visual only — no kinematic effect',
  },
};


// ─────────────────────────────────────────────────
// §3  ROBOT ARM — kinematic structure
// ─────────────────────────────────────────────────

export const ROBOT_ARM = {
  model: 'xArm6' as const,
  defaultVariant: '6-6' as const,

  /** STL mesh rotation base (degrees): all meshes get MESHS_ROTATION[i] + this. */
  meshRotationBaseDeg: [-90, 0, -90] as const,

  /** Workspace (from xarm_service/app/config.py). mm. */
  workspace: {
    dimensions: [400, 900, 1200] as const,   // X × Y × Z mm
    basePositionInWS: [150, 450, 0] as const, // mm
  },

  xarmIp: '192.168.1.220',
  tcpSpeedMaxMmS: 150,
  tcpAccelMaxMmS2: 800,
} as const;


// ─────────────────────────────────────────────────
// §4  LIFT — Igus linear actuator
// ─────────────────────────────────────────────────

export const LIFT = {
  /** World units per 10,000 motor units. */
  worldUnitsPerTenK: 1.55,

  /** Meters per 10,000 motor units (Y component). */
  metersPerTenK: 1.55 / 20,  // 0.0775 m

  /** Motor unit range. */
  positionLimits: { min: 0, max: 120_000 },

  /**
   * Oblique direction vector (per 1 world unit of lift).
   * Lift does NOT move purely vertically.
   * In Three.js Y-up: dy=+1, dx=-1/3.5, dz=-1/2
   * In ROS Z-up: dz=+1, dx=0, dy=+1/2 (after axis swap)
   */
  directionPerL_threeYup: { dx: -1 / 3.5, dy: 1.0, dz: -1 / 2 },

  /** Magnitude multiplier for oblique travel. */
  directionMagnitudePerL: Math.sqrt(1 + 1 / 12.25 + 1 / 4),  // ≈ 1.154

  /**
   * groups[1] geometric base Y position (world units).
   * Calculated from groupsPosition[1][1] × SCALE_FACTOR = 0.267 × 20 = 5.34.
   * NOTE: Legacy production bundle used 5.0 (17mm error). We use the geometric value.
   */
  group1BaseY_wu: 0.267 * 20,  // 5.34 (FIXED from legacy 5.0)
} as const;


// ─────────────────────────────────────────────────
// §5  DEPTH CAMERA — RealSense D435
// ─────────────────────────────────────────────────

export const DEPTH_CAMERA = {
  model: 'RealSense D435' as const,
  mounting: 'eye-in-hand' as const,

  intrinsics: {
    fx: 380.4253845214844,
    fy: 380.4253845214844,
    cx: 324.824951171875,
    cy: 232.37411499023438,
    width: 640,
    height: 480,
  } satisfies CameraIntrinsics,

  depthCalibration: {
    k: 0.957,
    b: 45.2,   // mm offset
  } satisfies DepthCalibration,

  /** Frustum visualization range (meters). */
  frustum: { near: 0.105, far: 1.0 },

  /** Computed FOV from intrinsics (degrees). */
  fov: {
    h: 2 * Math.atan(640 / (2 * 380.4253845214844)) * (180 / Math.PI),   // ≈ 80.16°
    v: 2 * Math.atan(480 / (2 * 380.4253845214844)) * (180 / Math.PI),   // ≈ 64.47°
  },
} as const;


// ─────────────────────────────────────────────────
// §5b  DEPTH OVERLAY — point cloud rendering
// ─────────────────────────────────────────────────

export const DEPTH_OVERLAY: DepthOverlayConfig = {
  stridePx: 4,
  minRaw: 50,
  maxRaw: 2000,
  minDistanceM: 0.10,
  pixelRotationDeg: 90,
  flipX: false,
  flipY: false,
  swapPayloadWH: false,
  cloudMirrorVertical: true,
  cloudMirrorAxis: 'y',
  cloudRotationDeg: { rx: 0, ry: 0, rz: 0 },
  depthShotColor: [0.45, 0.75, 0.95],
};

export const DEPTH_OVERLAY_RENDER: PointCloudRenderConfig = {
  /** Screen-space point size in pixels. sizeAttenuation=false. */
  pointSizePx: 2.0,
  opacity: 1.0,
  depthTest: true,
  depthWrite: true,
};

export const VOXEL_RECORDING: VoxelRecordingConfig = {
  voxelSizeMm: 5,
  maxVoxels: 400_000,
  livePointSizeWu: 0.5,
  mapPointSizeWu: 0.5,
  mapOpacity: 0.9,
};


// ─────────────────────────────────────────────────
// §6  SCENE CAMERA — Three.js viewport
// ─────────────────────────────────────────────────

export const SCENE_CAMERA = {
  fov: 45,
  near: 0.1,
  far: 500,
  initialPosition: [20, 15, 20] as const,   // world units
  initialTarget: [0, 5, 0] as const,        // world units
  controls: {
    enableDamping: true,
    dampingFactor: 0.08,
    minDistance: 5,
    maxDistance: 200,
  },
} as const;


// ─────────────────────────────────────────────────
// §7  LIGHTS
// ─────────────────────────────────────────────────

export const LIGHTS = [
  { type: 'ambient' as const, color: 0xffffff, intensity: 0.5 },
  {
    type: 'directional' as const,
    color: 0xffffff,
    intensity: 0.9,
    position: [20, 40, 30] as const,
    castShadow: true,
  },
  {
    type: 'directional' as const,
    color: 0x8090ff,
    intensity: 0.3,
    position: [-30, 20, -30] as const,
    castShadow: false,
  },
] as const;


// ─────────────────────────────────────────────────
// §8  FLOOR / GRID
// ─────────────────────────────────────────────────

export const FLOOR = {
  gridSize: 80,
  gridDivisions: 24,
  gridColors: [0x303846, 0x232a36] as const,
  positionY: -5,        // world units
  realHeightM: 0,       // ROS Z = floor level
} as const;


// ─────────────────────────────────────────────────
// §9  AGV — Symovo
// ─────────────────────────────────────────────────

export const AGV = {
  model: 'Symovo' as const,
  ip: '192.168.1.100',
  protocol: 'https' as const,
  port: 443,
  basePath: '/v0',
  robotNumber: 15,
} as const;

export const AGV_VISUALIZATION = {
  enabled: true,
  mapLayer: {
    mapListUrl: '/api/v1/symovo/map',
    mapMetaUrlTemplate: '/api/v1/symovo/map/{map_id}',
    mapImageUrlTemplate: '/api/v1/symovo/map/{map_id}/full.png',
    preferredMapId: 1,
    textureRotationDeg: 0,
    planePosXM: 15.06,
    planePosYM: -12.84,
    planePosZM: 0,
    planeRxDeg: 0,
    planeRyDeg: -90,
    planeRzDeg: 0,
    texOffsetXM: 0,
    texOffsetYM: 0,
    texYawDeg: 0,
    opacity: 0.8,
    yOffset_wu: -4.98,
  },
  robotStatus: {
    url: '/api/v1/robot/status',
    pollMs: 500,
  },
  pose: {
    anchorMode: 'agv_center' as const,
    swapXY: true,
    invertX: false,
    invertY: true,
    invertYaw: false,
    yawOffsetDeg: 0,
  },
} as const;


// ─────────────────────────────────────────────────
// §10  SCENE BACKGROUND / RENDERER
// ─────────────────────────────────────────────────

export const SCENE = {
  background: '#0f1116',
  renderer: {
    antialias: true,
    alpha: true,
    shadowMapEnabled: true,
    shadowMapType: 'PCFSoftShadowMap' as const,
    pixelRatioMax: 2,
  },
} as const;


// ─────────────────────────────────────────────────
// §11  API ENDPOINTS
// ─────────────────────────────────────────────────

export const API = {
  depthFrame: '/api/v1/depth_camera/depth/frame?format=json',
  depthFrameColorOverlay: '/api/v1/depth_camera/depth/frame_color_overlay?format=json',
  depthMap: {
    save: '/api/v1/depth_map/save',
    load: '/api/v1/depth_map/load',
    delete: '/api/v1/depth_map',
    info: '/api/v1/depth_map/info',
  },
  robotStatus: '/api/v1/robot/status',
  xarmStatus: '/api/v1/xarm/status',
  xarmJoints: '/api/v1/xarm/joints_position',
  symovo: {
    mapList: '/api/v1/symovo/map',
    mapMeta: '/api/v1/symovo/map/{map_id}',
    mapImage: '/api/v1/symovo/map/{map_id}/full.png',
  },
} as const;


// ─────────────────────────────────────────────────
// §12  DEBUG QUERY PARAMS
// ─────────────────────────────────────────────────

export const DEBUG_PARAMS = {
  transform: 'transform',
  axes: 'axes',
  cameraFrustum: 'camera_frustum',
  depthCloud: 'depth_cloud',
  coords: 'coords',
  agvMap: 'agv_map',
} as const;
