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
): PointCloud {
  const stride = overlay.stridePx;
  const rotDeg = overlay.pixelRotationDeg;

  // Maximum possible points (pre-allocate)
  const maxPts = Math.ceil(frameWidth / stride) * Math.ceil(frameHeight / stride);
  const positions = new Float32Array(maxPts * 3);
  const colors = new Float32Array(maxPts * 3);
  let count = 0;

  // Determine effective width/height after rotation (used for bounds)
  rotatePixel(0, 0, frameWidth, frameHeight, rotDeg);

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
      if (rawMm < overlay.minRaw || rawMm > overlay.maxRaw) continue;

      // Apply calibration
      const calMm = calibrateDepthMm(rawMm, calibration);
      const depthM = calMm / 1000;

      // Skip too close
      if (depthM < overlay.minDistanceM) continue;

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
  };
}
