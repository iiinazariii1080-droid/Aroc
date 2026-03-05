/**
 * scene-config.js — Declarative hybrid scene configuration
 * Version: 20260222b
 *
 * Central source of truth for all scene entities, transforms,
 * and coordinate conventions. Replaces magic numbers scattered
 * across arm3d-runtime.js and main.b58ea3f.js.
 *
 * Loaded as a plain <script> BEFORE arm3d-runtime.js.
 * Exposes window.SCENE_CONFIG for consumption by downstream modules.
 *
 * Coordinate conventions:
 *   ROS  — Z-up, meters  (X-forward, Y-left, Z-up)
 *   Three.js — Y-up, world units  (X-right, Y-up, Z-toward-camera)
 *   1 world unit = 50 mm = 0.05 m  (SCALE_FACTOR = 20)
 */

(function () {
  "use strict";

  // ─────────────────────────────────────────────────
  // §1  WORLD  — global coordinate conventions
  // ─────────────────────────────────────────────────
  const WORLD = {
    convention: "Z-up",          // target world convention (ROS-style)
    units: "meters",
    coordinateSystem: "ROS",     // X-forward, Y-left, Z-up
    SCALE_FACTOR: 20,            // legacy: 1 m → 20 world units (Three.js)
    // Derived helpers
    WU_PER_METER: 20,
    MM_PER_WU: 50,               // 1 world unit = 50 mm
  };


  // ─────────────────────────────────────────────────
  // §2  STATIC TRANSFORMS  (placeholders where noted)
  // ─────────────────────────────────────────────────
  //
  // Full chain:
  //   T_world (map) → T_agv → T_arm_mount → T_lift → FK(J1..J6)
  //     → T_flange → T_gripper | T_camera
  //
  // Notation: { tx,ty,tz } in meters, { rx,ry,rz } in degrees.
  //
  const TRANSFORMS = {

    // Canonical arm-base visual transform in world units/degrees.
    // This is treated as the zero reference for runtime debug deltas.
    armBaseCanonical: {
      position_wu: { x: 0, y: 10.5, z: -2 },
      rotation_deg: { x: 30, y: -30, z: 0 },
      _status: "KNOWN",
      _note: "Canonical zero reference (2026-02-20)",
    },

    // T_agv_armbase — arm base position on AGV chassis.
    // Source: physical mounting drawings (USER MUST FILL).
    agvToArmBase: {
      tx: 0, ty: 0, tz: 0,     // meters, in AGV body frame
      rx: 0, ry: 0, rz: 0,     // degrees
      _status: "PLACEHOLDER",
    },

    // T_flange_camera — D435 position relative to tool flange.
    // The D435 is mounted on the SIDE of the vacuum gripper body,
    // pointing SIDEWAYS (perpendicular to the gripper/suction axis).
    //
    // In toolGroup local frame (Three.js Y-up):
    //   T:Y = gripper axis (outward from flange toward suction cup)
    //   T:X, T:Z = perpendicular directions
    //
    // Position: 44mm along X, -168mm along Y (toward gripper tip), -27mm along Z
    // Rotation: rx=-90° rotates frustum to match physical D435 orientation
    //
    flangeToCamera: {
      tx: 0.030, ty: -0.15, tz: -0.030,  // meters, in toolGroup local (Three.js)
      rx: -90, ry: 0, rz: -90,              // degrees — measured from physical mount
      _status: "KNOWN",
      _note: "Calibrated 2026-02-20. P:44/-168/-27 mm  R:-90/0/-90 deg.",
    },

    // T_flange_gripper — vacuum gripper offset from flange.
    // Currently assumed zero offset (gripper STL is placed at tool group origin).
    flangeToGripper: {
      tx: 0, ty: 0, tz: 0,
      rx: 0, ry: 0, rz: 0,
      _status: "KNOWN",
    },

    // T_base_plate — base plate visual offset from groups[0].
    // From loadBaseStl(): position = [-0.01, 0.05, -0.02] m (pre-scale).
    basePlate: {
      tx: -0.01, ty: 0.05, tz: -0.02,  // meters (pre-scale)
      rx: 0, ry: 0, rz: 0,
      _status: "KNOWN",
      _note: "Visual only — no kinematic effect",
    },
  };


  // ─────────────────────────────────────────────────
  // §3  ROBOT ARM  — kinematic structure
  // ─────────────────────────────────────────────────
  const ROBOT_ARM = {
    model: "xArm6",
    variant: "6-6",

    // Pre-scale positions (meters). Same as ARM_SPECS["6-6"].groupsPosition.
    // groups[0] = base, groups[1..6] = joints J1..J6.
    groupsPosition: [
      [0, -0.25, 0],           // group 0 (base frame)
      [0,  0.267, 0],          // group 1 (J1) — 267 mm from base
      [0,  0, 0],              // group 2 (J2) — coincident
      [0,  0.285, -0.0535],    // group 3 (J3) — link length √(285²+53.5²) = 290.0 mm
      [0, -0.3425, -0.0775],   // group 4 (J4) — link length √(342.5²+77.5²) = 351.2 mm
      [0,  0, 0],              // group 5 (J5) — coincident
      [0, -0.097, -0.076],     // group 6 (J6) — link length √(97²+76²) = 123.2 mm
    ],

    // Euclidean link lengths (mm), rounded. Used for FK verification.
    // Index = joint number (1-based). 0 = base-to-J1.
    linkLengths: [267.0, 0, 290.0, 351.2, 0, 123.2],

    // Joint rotation axes in Three.js frame (Y-up).
    // From updateFn: J1=rot.y, J2=rot.x, J3=rot.x, J4=rot.y, J5=rot.x, J6=rot.y
    jointAxes: ["Y", "X", "X", "Y", "X", "Y"],

    // Joint angle signs and offsets (from updateFn code):
    //   J1: rotation.y =  rad(M[0] - 180)   → offset -180°, positive direction
    //   J2: rotation.x = -rad(M[1])          → negated
    //   J3: rotation.x = -rad(M[2])          → negated
    //   J4: rotation.y = -rad(M[3])          → negated
    //   J5: rotation.x = -rad(M[4])          → negated
    //   J6: rotation.y = -rad(M[5])          → negated
    jointSignsAndOffsets: [
      { sign: +1, offsetDeg: -180 },  // J1
      { sign: -1, offsetDeg:  0 },    // J2
      { sign: -1, offsetDeg:  0 },    // J3
      { sign: -1, offsetDeg:  0 },    // J4
      { sign: -1, offsetDeg:  0 },    // J5
      { sign: -1, offsetDeg:  0 },    // J6
    ],

    // STL mesh rotation base: all meshes get MESHS_ROTATION[i] + [-90, 0, -90] deg
    meshRotationBase: [-90, 0, -90],

    // Mount (tilt, rotation) — applied to rootGroup.
    mountDegrees: [0, 0],

    // Workspace (from xarm_service/app/config.py)
    workspace: {
      dimensions: [400, 900, 1200],        // mm: X × Y × Z
      basePositionInWS: [150, 450, 0],     // mm
      rotation: [0, 0, 0],                 // degrees — axes aligned
    },
    xarmIp: "192.168.1.220",
    tcpSpeedMax: 150,      // mm/s
    tcpAccelMax: 800,      // mm/s²
  };


  // ─────────────────────────────────────────────────
  // §4  LIFT  — Igus linear actuator
  // ─────────────────────────────────────────────────
  //
  // The lift does NOT move purely vertically.
  // In Three.js Y-up world units, the displacement vector is:
  //
  //   dy = +L               (up)
  //   dx = -L / 3.5         (backward/left)
  //   dz = -L / 2           (sideways)
  //
  // where L = (motorUnits / 10000) × 1.55  [world units].
  //
  // The effective displacement magnitude per unit L:
  //   |d| = √(1 + 1/12.25 + 1/4) = √1.332 ≈ 1.154
  //
  // So actual travel is 15.4% longer than the Y component alone.
  //
  // IMPORTANT: groups[1].position.y uses `L + 5.0` (hardcoded).
  //   The geometric default is 0.267 × 20 = 5.34 wu.
  //   There is a 0.34 wu (17 mm) discrepancy — carried from production bundle.
  //   liftVisualNode (link0 mesh) uses `L` without the +5.0 base.
  //
  const LIFT = {
    // Raw formula constants (world units)
    worldUnitsPerTenK: 1.55,              // 1.55 wu per 10,000 motor units
    metersPerTenK: 1.55 / 20,            // 0.0775 m per 10,000 motor units = 77.5 mm

    // Motor unit range
    positionLimits: { min: 0, max: 120000 },

    // At max lift (120,000 mU):
    //   L_max = 12 × 1.55 = 18.6 wu = 0.93 m (Y component)
    //   Euclidean displacement = 18.6 × 1.154 ≈ 21.47 wu = 1.074 m
    maxTravelY_wu: 18.6,
    maxTravelY_m: 0.93,
    maxTravelEuclidean_m: 1.074,

    // groups[1] base offset (hardcoded in production bundle)
    group1BaseY_wu: 5.0,
    // Geometric value from groupsPosition[1] (for reference/comparison)
    group1GeometricY_wu: 0.267 * 20,  // = 5.34

    // Oblique lift direction vector (normalized factor per L wu):
    directionPerL: { dx: -1/3.5, dy: 1.0, dz: -1/2 },
    directionMagnitudePerL: Math.sqrt(1 + 1/12.25 + 1/4),  // ≈ 1.154

    /**
     * Convert motor units to world-unit displacement (L).
     * @param {number} motorUnits - raw motor position (0..120000)
     * @returns {number} L in world units
     */
    toWorldUnits(motorUnits) {
      return ((motorUnits || 0) / 10000) * 1.55;
    },

    /**
     * Convert motor units to meters (Y component only).
     * @param {number} motorUnits
     * @returns {number} meters
     */
    toMetersY(motorUnits) {
      return ((motorUnits || 0) / 10000) * 1.55 / 20;
    },

    /**
     * Convert motor units to euclidean displacement in meters.
     * @param {number} motorUnits
     * @returns {number} meters
     */
    toMetersEuclidean(motorUnits) {
      return this.toMetersY(motorUnits) * this.directionMagnitudePerL;
    },

    /**
     * Get full lift displacement vector in world units.
     * @param {number} motorUnits
     * @returns {{x: number, y: number, z: number}} world units
     */
    toWorldVector(motorUnits) {
      const L = this.toWorldUnits(motorUnits);
      return {
        x: L * this.directionPerL.dx,
        y: L * this.directionPerL.dy,
        z: L * this.directionPerL.dz,
      };
    },
  };


  // ─────────────────────────────────────────────────
  // §5  DEPTH CAMERA  — RealSense D435 (eye-in-hand)
  // ─────────────────────────────────────────────────
  const DEPTH_CAMERA = {
    model: "RealSense D435",

    // Intrinsics (from xarm_service/app/config.py)
    intrinsics: {
      fx: 380.4253845214844,
      fy: 380.4253845214844,
      cx: 324.824951171875,
      cy: 232.37411499023438,
      width: 640,
      height: 480,
    },

    // Depth processing
    depthScale: 0.9,                             // raw → mm
    depthCalibration: { k: 0.957, b: 45.2 },    // true_mm = k × raw_mm + b

    // FOV — canonical pinhole formula: 2 × atan( dimension / (2 × focal_length) )
    // HFOV = 2 × atan(640 / (2 × 380.425)) = 2 × atan(0.8412) ≈ 80.16°
    // VFOV = 2 × atan(480 / (2 × 380.425)) = 2 × atan(0.6309) ≈ 64.47°
    fov: {
      h: 2 * Math.atan(640 / (2 * 380.4253845214844)) * (180 / Math.PI),  // ≈ 80.16°
      v: 2 * Math.atan(480 / (2 * 380.4253845214844)) * (180 / Math.PI),  // ≈ 64.47°
    },

    // Frustum visualization range (meters)
    frustum: { near: 0.105, far: 1.0 },

    // Mounting
    mounting: "eye-in-hand",         // attached to xArm tool flange
    // Camera axis mapping (legacy, from depth_service.py L273-276):
    //   tool_x = -cam_dy,  tool_y = cam_dx
    axisMapping: { tool_x: "-cam_y", tool_y: "cam_x" },

    // Data endpoint
    endpoint: "http://192.168.1.55:8000",
    endpoints: {
      depthFrame: "/depth/frame",     // 614400 bytes, uint16
      depthPixel: "/depth",           // ?x=&y= → single pixel
      colorFrame: "/video_feed",
    },
  };


  // ─────────────────────────────────────────────────
  // §5b DEPTH OVERLAY — point cloud on camera mount
  // ─────────────────────────────────────────────────
  const DEPTH_OVERLAY = {
    // Fetch through API gateway (same-origin)
    frameJsonUrl: "/api/v1/depth_camera/depth/frame?format=json",
    // Aligned RGBD endpoint — returns rgb_data (RGB24) + depth_data (Z16) in one JSON
    frameColorOverlayJsonUrl: "/api/v1/depth_camera/depth/frame_color_overlay?format=json",

    // false = Shot and Record use uniform depthShotColor;
    // true  = always try to fetch colour overlay (old behaviour)
    alwaysFetchColor: false,

    // Uniform colour for depth-only shots / recording (R, G, B  0..1)
    depthShotColor: [0.45, 0.75, 0.95],   // light steel-blue

    // Runtime budget
    fps: 2,
    stridePx: 4,

    // Filtering limits (raw uint16 input from endpoint)
    minRaw: 50,
    maxRaw: 2000,

    // Point rendering (world units)
    pointSize_wu: 0.5,
    opacity: 0.9,

    // Fixed cloud orientation correction (degrees)
    cloudRotationDeg: { rx: 0, ry: 0, rz: 0 },

    // Hard near-plane cutoff for recording/rendering (meters)
    minDistanceM: 0.10,

    // Cloud recording / mapping (world-frame voxel map)
    recording: {
      voxelSizeMm: 5,
      maxVoxels: 400000,
      mapPointSize_wu: 0.5,
      mapOpacity: 0.9,
      livePointSize_wu: 0.5,
    },

    // Optional frame transforms for endpoint-specific layout
    // Keep raw payload indexing as-is; apply only vertical mirror.
    swapPayloadWH: false,
    pixelRotationDeg: 90,
    flipX: false,
    flipY: false,

    // Geometric mirror in point-cloud local 3D frame (after rotation correction)
    cloudMirrorVertical: true,
    cloudMirrorAxis: "y",
  };


  // ─────────────────────────────────────────────────
  // §6  SCENE CAMERA  — Three.js viewport
  // ─────────────────────────────────────────────────
  const SCENE_CAMERA = {
    fov: 45,
    near: 0.1,
    far: 500,
    // All in world units (Three.js Y-up)
    initialPosition: [20, 15, 20],
    initialTarget: [0, 5, 0],
    controls: {
      enableDamping: true,
      dampingFactor: 0.08,
      minDistance: 5,
      maxDistance: 200,
    },
  };


  // ─────────────────────────────────────────────────
  // §7  LIGHTS
  // ─────────────────────────────────────────────────
  const LIGHTS = [
    {
      type: "ambient",
      color: 0xffffff,
      intensity: 0.5,
    },
    {
      type: "directional",
      color: 0xffffff,
      intensity: 0.9,
      position: [20, 40, 30],     // world units
      castShadow: true,
      // Note: PCFSoftShadowMap is set on the renderer, not the light.
    },
    {
      type: "directional",
      color: 0x8090ff,
      intensity: 0.3,
      position: [-30, 20, -30],   // world units
      castShadow: false,
    },
  ];


  // ─────────────────────────────────────────────────
  // §8  FLOOR / GRID
  // ─────────────────────────────────────────────────
  const FLOOR = {
    gridSize: 80,              // world units
    gridDivisions: 24,
    gridColors: [0x303846, 0x232a36],
    positionY: -5,             // world units (Three.js Y-up)
    realHeight_m: 0,           // meters (ROS Z = floor level)
  };


  // ─────────────────────────────────────────────────
  // §9  AGV  — Symovo platform
  // ─────────────────────────────────────────────────
  const AGV = {
    model: "Symovo",
    ip: "192.168.1.100",        // verified from nav2adapter/app/config.py
    protocol: "https",
    port: 443,
    basePath: "/v0",
    robotNumber: 15,            // from nav2adapter config

    // Lidar
    lidar: {
      type: "built-in",
      dataSource: "AGV API",
      height_m: 0,              // PLACEHOLDER — height above floor, meters
      _status: "PLACEHOLDER",
    },

    // nav2adapter service (separate from AGV itself)
    adapterService: {
      port: 7905,
      host: "localhost",
    },

    // Navigation frame
    navFrame: {
      frameId: "map",
      convention: "Z-up",       // x, y, theta
      units: "meters",
    },

    // Dimensions (PLACEHOLDER — need measurements)
    dimensions: {
      length: 0,   // mm
      width: 0,    // mm
      height: 0,   // mm
      _status: "PLACEHOLDER",
    },
  };


  // ─────────────────────────────────────────────────
  // §9b AGV VISUALIZATION (3D map + unified status)
  // ─────────────────────────────────────────────────
  const AGV_VISUALIZATION = {
    enabled: true,
    mapLayer: {
      mapListUrl: "/api/v1/symovo/map",
      mapMetaUrlTemplate: "/api/v1/symovo/map/{map_id}",
      mapImageUrlTemplate: "/api/v1/symovo/map/{map_id}/full.png",
      preferredMapId: 1,
      // Rotates the map IMAGE in UV space (base rotation, not slider).
      textureRotationDeg: 0,
      // Plane translation sliders in metres relative to map top-left anchor.
      // Startup defaults: Position (15.06, -12.84, 0), Rotation (0, -90, 0).
      planePosXM: 15.06,
      planePosYM: -12.84,
      planePosZM: 0,
      // Plane rotation sliders around map top-left origin
      // (origin derived from map offsets: x=offsetX, y=offsetY+heightM).
      planeRxDeg: 0,
      planeRyDeg: -90,
      planeRzDeg: 0,
      // Texture sliders (UV shift in metres + rotation in degrees)
      texOffsetXM: 0,
      texOffsetYM: 0,
      texYawDeg: 0,
      opacity: 0.8,
      yOffset_wu: -4.98,
    },
    robotStatus: {
      url: "/api/v1/robot/status",
      pollMs: 500,
    },
    pose: {
      anchorMode: "agv_center", // agv_center | arm_base
      swapXY: true,
      invertX: false,
      invertY: true,
      invertYaw: false,
      yawOffsetDeg: 0,
    },
  };


  // ─────────────────────────────────────────────────
  // §10  SCENE BACKGROUND
  // ─────────────────────────────────────────────────
  const SCENE = {
    background: "#0f1116",
    // Renderer settings
    renderer: {
      antialias: true,
      alpha: true,
      shadowMapEnabled: true,
      shadowMapType: "PCFSoftShadowMap",
      pixelRatioMax: 2,
    },
  };


  // ─────────────────────────────────────────────────
  // §11  DEBUG QUERY PARAMS
  // ─────────────────────────────────────────────────
  const DEBUG_PARAMS = {
    transform: "transform",        // existing: model rotation/position sliders
    axes: "axes",                  // world axes (RGB = XYZ)
    axesAll: "axes=all",           // + arm base + TCP + camera frame axes
    cameraFrustum: "camera_frustum", // D435 frustum wireframe
    depthCloud: "depth_cloud",       // D435 live depth point cloud
    agv: "agv",                    // AGV outline
    lidarRing: "lidar_ring",       // lidar height ring
    coords: "coords",             // TCP coordinates HUD
    agvMap: "agv_map",            // AGV map layer + unified robot pose polling
  };


  // ═════════════════════════════════════════════════
  //  Export as window global
  // ═════════════════════════════════════════════════
  window.SCENE_CONFIG = Object.freeze({
    WORLD,
    TRANSFORMS,
    ROBOT_ARM,
    LIFT,
    DEPTH_CAMERA,
    DEPTH_OVERLAY,
    SCENE_CAMERA,
    LIGHTS,
    FLOOR,
    AGV,
    AGV_VISUALIZATION,
    SCENE,
    DEBUG_PARAMS,
  });

  console.log("[scene-config] v20260222b loaded", window.SCENE_CONFIG);
})();
