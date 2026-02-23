/**
 * coord-utils.js — Coordinate conversion utilities for hybrid scene
 * Version: 20260219a
 *
 * Provides bidirectional conversion between:
 *   ROS  (Z-up, meters)  ↔  Three.js  (Y-up, world units)
 *
 * Mapping (rotation -90° around shared X axis):
 *
 *   Three.x =  ROS.x        ROS.x =  Three.x
 *   Three.y =  ROS.z        ROS.y = -Three.z
 *   Three.z = -ROS.y        ROS.z =  Three.y
 *
 * Scale:
 *   worldUnits = meters × 20
 *   meters     = worldUnits / 20
 *
 * Scene orientation note:
 *   ROS X-forward → Three.js X (screen right)
 *   ROS Y-left    → Three.js -Z (into screen)
 *   ROS Z-up      → Three.js Y (screen up)
 *   The default camera at (20,15,20) views the robot from its front-right.
 *
 * Loaded as a plain <script> BEFORE arm3d-runtime.js.
 * Exposes window.CoordUtils.
 */

(function () {
  "use strict";

  const CFG = window.SCENE_CONFIG;
  if (!CFG) {
    console.error("[coord-utils] window.SCENE_CONFIG not found — load scene-config.js first");
    return;
  }
  const S = CFG.WORLD.SCALE_FACTOR;  // 20


  // ─────────────────────────────────────────────────
  // §1  Axis swaps (no scaling)
  // ─────────────────────────────────────────────────

  /**
   * ROS (Z-up) → Three.js (Y-up). Pure axis swap, no scaling.
   * @param {number} x - ROS X (forward)
   * @param {number} y - ROS Y (left)
   * @param {number} z - ROS Z (up)
   * @returns {[number, number, number]} [three.x, three.y, three.z]
   */
  function rosToThree(x, y, z) {
    return [x, z, -y];
  }

  /**
   * Three.js (Y-up) → ROS (Z-up). Pure axis swap, no scaling.
   * @param {number} x - Three.js X
   * @param {number} y - Three.js Y (up)
   * @param {number} z - Three.js Z
   * @returns {[number, number, number]} [ros.x, ros.y, ros.z]
   */
  function threeToRos(x, y, z) {
    return [x, -z, y];
  }


  // ─────────────────────────────────────────────────
  // §2  Scale conversions
  // ─────────────────────────────────────────────────

  /**
   * Meters → Three.js world units.
   * @param {number} m - value in meters
   * @returns {number} world units
   */
  function metersToWorld(m) {
    return m * S;
  }

  /**
   * Three.js world units → meters.
   * @param {number} wu - world units
   * @returns {number} meters
   */
  function worldToMeters(wu) {
    return wu / S;
  }

  /**
   * Millimeters → Three.js world units.
   * @param {number} mm
   * @returns {number} world units
   */
  function mmToWorld(mm) {
    return mm / CFG.WORLD.MM_PER_WU;  // mm / 50
  }

  /**
   * Three.js world units → millimeters.
   * @param {number} wu
   * @returns {number} mm
   */
  function worldToMm(wu) {
    return wu * CFG.WORLD.MM_PER_WU;  // wu × 50
  }


  // ─────────────────────────────────────────────────
  // §3  Combined conversions (axis swap + scale)
  // ─────────────────────────────────────────────────

  /**
   * ROS meters → Three.js world units (full conversion).
   * @param {number} x - ROS X (meters)
   * @param {number} y - ROS Y (meters)
   * @param {number} z - ROS Z (meters)
   * @returns {[number, number, number]} Three.js world units
   */
  function rosMetersToThreeWorld(x, y, z) {
    const [tx, ty, tz] = rosToThree(x, y, z);
    return [metersToWorld(tx), metersToWorld(ty), metersToWorld(tz)];
  }

  /**
   * Three.js world units → ROS meters (full inverse).
   * @param {number} x - Three.js X (world units)
   * @param {number} y - Three.js Y (world units)
   * @param {number} z - Three.js Z (world units)
   * @returns {[number, number, number]} ROS meters
   */
  function threeWorldToRosMeters(x, y, z) {
    const [mx, my, mz] = [worldToMeters(x), worldToMeters(y), worldToMeters(z)];
    return threeToRos(mx, my, mz);
  }

  /**
   * ROS mm → Three.js world units (convenience for xArm data).
   * @param {number} x - ROS X (mm)
   * @param {number} y - ROS Y (mm)
   * @param {number} z - ROS Z (mm)
   * @returns {[number, number, number]} Three.js world units
   */
  function rosMmToThreeWorld(x, y, z) {
    return rosMetersToThreeWorld(x / 1000, y / 1000, z / 1000);
  }

  /**
   * Three.js world units → ROS mm (convenience).
   * @param {number} x - Three.js X (world units)
   * @param {number} y - Three.js Y (world units)
   * @param {number} z - Three.js Z (world units)
   * @returns {[number, number, number]} ROS mm
   */
  function threeWorldToRosMm(x, y, z) {
    const [rx, ry, rz] = threeWorldToRosMeters(x, y, z);
    return [rx * 1000, ry * 1000, rz * 1000];
  }


  // ─────────────────────────────────────────────────
  // §4  Rotation conversions
  // ─────────────────────────────────────────────────

  const DEG = Math.PI / 180;
  const RAD = 180 / Math.PI;

  /**
   * ROS Euler (RPY in radians, Z-up) → Three.js Euler (Y-up) in radians.
   * Applies the same axis permutation as position.
   * ROS: roll=X, pitch=Y, yaw=Z  →  Three: X, Z, -Y
   * @param {number} roll  - rotation around ROS X (rad)
   * @param {number} pitch - rotation around ROS Y (rad)
   * @param {number} yaw   - rotation around ROS Z (rad)
   * @returns {[number, number, number]} [rx, ry, rz] Three.js radians
   */
  function rosRPYToThree(roll, pitch, yaw) {
    return [roll, yaw, -pitch];
  }

  /**
   * Three.js Euler (radians) → ROS RPY (radians).
   * @param {number} rx - Three.js X rotation
   * @param {number} ry - Three.js Y rotation
   * @param {number} rz - Three.js Z rotation
   * @returns {[number, number, number]} [roll, pitch, yaw] ROS radians
   */
  function threeToRosRPY(rx, ry, rz) {
    return [rx, -rz, ry];
  }


  // ─────────────────────────────────────────────────
  // §5  Depth camera helpers
  // ─────────────────────────────────────────────────

  /**
   * Convert D435 pixel (u, v, depth_mm) to 3D point in camera frame (meters).
   * Uses pinhole model: X = (u - cx) × Z / fx, Y = (v - cy) × Z / fy
   * @param {number} u - pixel column
   * @param {number} v - pixel row
   * @param {number} depth_raw_mm - raw depth value in mm (before calibration)
   * @returns {{x: number, y: number, z: number}} camera-frame meters
   */
  function depthPixelToCamera(u, v, depth_raw_mm) {
    const intr = CFG.DEPTH_CAMERA.intrinsics;
    const cal = CFG.DEPTH_CAMERA.depthCalibration;
    // Apply calibration: true = k × raw + b
    const z_mm = cal.k * depth_raw_mm + cal.b;
    const z_m = z_mm / 1000;
    return {
      x: ((u - intr.cx) / intr.fx) * z_m,
      y: ((v - intr.cy) / intr.fy) * z_m,
      z: z_m,
    };
  }

  /**
   * Get D435 FOV in degrees using canonical pinhole formula.
   * @returns {{h: number, v: number}}
   */
  function getCameraFOV() {
    const intr = CFG.DEPTH_CAMERA.intrinsics;
    return {
      h: 2 * Math.atan(intr.width  / (2 * intr.fx)) * RAD,
      v: 2 * Math.atan(intr.height / (2 * intr.fy)) * RAD,
    };
  }


  // ─────────────────────────────────────────────────
  // §6  Lift helpers
  // ─────────────────────────────────────────────────

  /**
   * Get groups[1] absolute position for a given lift value (world units).
   * Matches the production updateFn exactly:
   *   y = L + 5.0,  x = -L/3.5,  z = -L/2
   * @param {number} motorUnits
   * @returns {{x: number, y: number, z: number}} world units
   */
  function liftToGroup1Position(motorUnits) {
    const L = CFG.LIFT.toWorldUnits(motorUnits);
    return {
      x: -(L / 3.5),
      y: L + CFG.LIFT.group1BaseY_wu,
      z: -(L / 2),
    };
  }

  /**
   * Get liftVisualNode position for a given lift value (world units).
   * Same oblique vector but WITHOUT the +5.0 base offset.
   * @param {number} motorUnits
   * @returns {{x: number, y: number, z: number}} world units
   */
  function liftToVisualNodePosition(motorUnits) {
    const L = CFG.LIFT.toWorldUnits(motorUnits);
    return {
      x: -(L / 3.5),
      y: L,
      z: -(L / 2),
    };
  }


  // ═════════════════════════════════════════════════
  //  Export
  // ═════════════════════════════════════════════════
  window.CoordUtils = Object.freeze({
    // Axis swaps
    rosToThree,
    threeToRos,
    // Scale
    metersToWorld,
    worldToMeters,
    mmToWorld,
    worldToMm,
    // Combined
    rosMetersToThreeWorld,
    threeWorldToRosMeters,
    rosMmToThreeWorld,
    threeWorldToRosMm,
    // Rotation
    rosRPYToThree,
    threeToRosRPY,
    DEG, RAD,
    // Depth camera
    depthPixelToCamera,
    getCameraFOV,
    // Lift
    liftToGroup1Position,
    liftToVisualNodePosition,
  });

  console.log("[coord-utils] v20260219a loaded");
})();
