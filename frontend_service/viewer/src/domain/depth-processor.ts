/**
 * depth-processor.ts — Pinhole camera unprojection and point cloud generation.
 *
 * Domain layer — no Three.js dependency.
 * Processes raw D435 depth frames into camera-local point clouds.
 *
 * Pipeline:
 *   1. Read raw uint16 depth pixel
 *   2. Apply linear calibration: mm = k × raw_mm + b
 *   3. Skip if outside [minRaw, maxRaw] or < minDistanceM
 *   4. Unproject using pinhole model: (u,v,d) → (X,Y,Z) in camera frame
 *   5. Apply pixel rotation (90° hardware mount correction)
 *   6. Apply mirror/flip corrections
 *   7. Output: Float32Array of XYZ positions + RGB colors
 */

import type { CameraIntrinsics, DepthCalibration, DepthOverlayConfig, PointCloud } from '@/types/depth';

// ─── Near-black pixel filter ──────────────────────

/** Default RGB threshold for near-black pixel rejection. */
export const NEAR_BLACK_THRESHOLD = 12;
/** Hard near-depth clamp to suppress zero/noise points close to camera. */
export const HARD_MIN_DISTANCE_M = 0.05;

/**
 * Returns true if the RGB pixel is near-black (all channels ≤ threshold).
 * Near-black pixels from the D435 typically indicate sensor noise or
 * invalid readings and should be rejected before unprojection.
 */
export function isNearBlackRgb(r: number, g: number, b: number, threshold = NEAR_BLACK_THRESHOLD): boolean {
  return r <= threshold && g <= threshold && b <= threshold;
}

// ─── Calibration ──────────────────────────────────

/**
 * Apply linear depth calibration: true_mm = k × raw + b.
 */
export function calibrateDepthMm(rawMm: number, cal: DepthCalibration): number {
  return cal.k * rawMm + cal.b;
}

// ─── Pixel rotation ───────────────────────────────

/**
 * Rotate pixel coordinates by 90° increments.
 * Returns new (u, v) and effective (width, height) after rotation.
 */
export function rotatePixel(
  u: number, v: number,
  w: number, h: number,
  rotationDeg: number,
): { u: number; v: number; w: number; h: number } {
  const r = ((rotationDeg % 360) + 360) % 360;
  switch (r) {
    case 90:  return { u: h - 1 - v, v: u, w: h, h: w };
    case 180: return { u: w - 1 - u, v: h - 1 - v, w, h };
    case 270: return { u: v, v: w - 1 - u, w: h, h: w };
    default:  return { u, v, w, h };
  }
}

// ─── Pinhole unprojection ─────────────────────────

/**
 * Unproject a depth pixel to a 3D point in camera frame (meters).
 *
 * Camera frame: X-right, Y-down, Z-depth (optical axis).
 *
 * @param u - pixel column (after rotation)
 * @param v - pixel row (after rotation)
 * @param depthM - calibrated depth in meters
 * @param intrinsics - camera intrinsic parameters (may need swapped cx/cy for rotated frame)
 */
export function unprojectPixel(
  u: number, v: number, depthM: number,
  fx: number, fy: number, cx: number, cy: number,
): { x: number; y: number; z: number } {
  return {
    x: (u - cx) / fx * depthM,
    y: (v - cy) / fy * depthM,
    z: depthM,
  };
}

// ─── Full frame processing ────────────────────────

/**
 * Process a raw depth frame into a camera-local point cloud.
 *
 * @param depthRaw     - Uint16Array of raw depth values (mm).
 * @param rgbRaw       - Optional Uint8Array of RGB24 interleaved data (same resolution).
 * @param frameWidth   - Width of the raw frame.
 * @param frameHeight  - Height of the raw frame.
 * @param intrinsics   - D435 camera intrinsics.
 * @param calibration  - Linear depth calibration.
 * @param overlay      - Depth overlay configuration.
 * @param depthScale   - Raw uint16 → mm factor (default 1.0).
 * @returns PointCloud in camera-local frame (meters).
 */
export function processDepthFrame(
  depthRaw: Uint16Array,
  rgbRaw: Uint8Array | null,
  frameWidth: number,
  frameHeight: number,
  intrinsics: CameraIntrinsics,
  calibration: DepthCalibration,
  overlay: DepthOverlayConfig,
  depthScale: number = 1.0,
  timestampMs: number = Date.now(),
): PointCloud {
  const stride = overlay.stridePx;
  const rotDeg = overlay.pixelRotationDeg;
  const totalPixels = frameWidth * frameHeight;

  // Guard: buffer must cover the full frame
  if (depthRaw.length < totalPixels) {
    return { positions: new Float32Array(0), colors: new Float32Array(0), count: 0, timestampMs };
  }

  // Maximum possible points (pre-allocate)
  const maxPts = Math.ceil(frameWidth / stride) * Math.ceil(frameHeight / stride);
  const positions = new Float32Array(maxPts * 3);
  const colors = new Float32Array(maxPts * 3);
  let count = 0;

  // Pre-compute rotated intrinsics cx/cy
  // When rotating 90°: (cx,cy) maps to (h-1-cy, cx) in the rotated frame
  let rFx = intrinsics.fx;
  let rFy = intrinsics.fy;
  let rCx = intrinsics.cx;
  let rCy = intrinsics.cy;
  const r = ((rotDeg % 360) + 360) % 360;
  if (r === 90) {
    rFx = intrinsics.fy;
    rFy = intrinsics.fx;
    rCx = frameHeight - 1 - intrinsics.cy;
    rCy = intrinsics.cx;
  } else if (r === 180) {
    rCx = frameWidth - 1 - intrinsics.cx;
    rCy = frameHeight - 1 - intrinsics.cy;
  } else if (r === 270) {
    rFx = intrinsics.fy;
    rFy = intrinsics.fx;
    rCx = intrinsics.cy;
    rCy = frameWidth - 1 - intrinsics.cx;
  }

  for (let v = 0; v < frameHeight; v += stride) {
    for (let u = 0; u < frameWidth; u += stride) {
      const idx = v * frameWidth + u;
      const rawMm = depthRaw[idx];

      // Skip invalid pixels
      if (rawMm === 0) continue;
      if (rawMm < overlay.minRaw || rawMm > overlay.maxRaw) continue;

      // Optional near-black RGB filter (disabled by default in MVP).
      if (overlay.rejectNearBlackRgb && rgbRaw && idx * 3 + 2 < rgbRaw.length) {
        const threshold = overlay.nearBlackThreshold ?? NEAR_BLACK_THRESHOLD;
        if (isNearBlackRgb(rgbRaw[idx * 3], rgbRaw[idx * 3 + 1], rgbRaw[idx * 3 + 2], threshold)) continue;
      }

      // Apply depth scale (raw uint16 → mm) then calibration
      const rawMmScaled = rawMm * depthScale;
      const calMm = calibrateDepthMm(rawMmScaled, calibration);
      if (!Number.isFinite(calMm) || calMm <= 0) continue;
      const depthM = calMm / 1000;

      // Skip too close (hard floor 5cm regardless of runtime/config drift)
      if (depthM < Math.max(overlay.minDistanceM, HARD_MIN_DISTANCE_M)) continue;

      // Rotate pixel coordinates
      const rot = rotatePixel(u, v, frameWidth, frameHeight, rotDeg);

      // Unproject using rotated intrinsics
      const pt = unprojectPixel(rot.u, rot.v, depthM, rFx, rFy, rCx, rCy);

      // Apply mirror corrections
      let px = pt.x;
      let py = pt.y;
      let pz = pt.z;

      if (overlay.flipX) px = -px;
      if (overlay.flipY) py = -py;
      if (overlay.cloudMirrorVertical) {
        if (overlay.cloudMirrorAxis === 'x') px = -px;
        else if (overlay.cloudMirrorAxis === 'y') py = -py;
        else if (overlay.cloudMirrorAxis === 'z') pz = -pz;
      }

      // Store position (camera frame, meters)
      const off = count * 3;
      positions[off] = px;
      positions[off + 1] = py;
      positions[off + 2] = pz;

      // Color: from RGB overlay or default depth-shot color
      if (rgbRaw && idx * 3 + 2 < rgbRaw.length) {
        colors[off] = rgbRaw[idx * 3] / 255;
        colors[off + 1] = rgbRaw[idx * 3 + 1] / 255;
        colors[off + 2] = rgbRaw[idx * 3 + 2] / 255;
      } else {
        colors[off] = overlay.depthShotColor[0];
        colors[off + 1] = overlay.depthShotColor[1];
        colors[off + 2] = overlay.depthShotColor[2];
      }

      count++;
    }
  }

  return {
    positions: positions.subarray(0, count * 3),
    colors: colors.subarray(0, count * 3),
    count,
    timestampMs,
  };
}

/**
 * Transform camera-local point cloud (meters) into world-frame points (Three.js world units).
 *
 * Local conversion matches legacy live-cloud behavior:
 *   local = scale(S, S, -S) * cameraPointMeters
 *   world = cameraWorldMatrix * local
 */
export function transformCloudToWorld(
  cloud: PointCloud,
  cameraWorldElements: ArrayLike<number>,
  scaleFactor: number,
): PointCloud {
  const { positions, colors, count, timestampMs } = cloud;
  if (count === 0) {
    return { positions, colors, count, timestampMs };
  }

  const e = cameraWorldElements;
  const s = scaleFactor;
  const worldPositions = new Float32Array(count * 3);

  for (let i = 0; i < count; i++) {
    const off = i * 3;

    const lx = positions[off] * s;
    const ly = positions[off + 1] * s;
    const lz = -positions[off + 2] * s;

    worldPositions[off] = e[0] * lx + e[4] * ly + e[8] * lz + e[12];
    worldPositions[off + 1] = e[1] * lx + e[5] * ly + e[9] * lz + e[13];
    worldPositions[off + 2] = e[2] * lx + e[6] * ly + e[10] * lz + e[14];
  }

  return {
    positions: worldPositions,
    colors,
    count,
    timestampMs,
  };
}
