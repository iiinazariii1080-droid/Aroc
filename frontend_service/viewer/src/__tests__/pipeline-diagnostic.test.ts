/**
 * pipeline-diagnostic.test.ts — Verifies that the centralized domain transform
 * produces expected Three.js world positions and that VoxelMap stores world points.
 *
 * Single authority path:
 *   - Domain: transformCloudToWorld(cloud, cameraWorld, S)
 *   - Voxel map: addCloud(worldPositions)
 *
 * Run: npx vitest run src/__tests__/pipeline-diagnostic.test.ts
 */
import { describe, it, expect } from 'vitest';
import {
  mat4Identity,
  degToRad,
} from '../domain/coord-utils';
import { transformCloudToWorld } from '../domain/depth-processor';
// eslint-disable-next-line @typescript-eslint/no-unused-vars
declare const process: { env: Record<string, string | undefined> };
import { VoxelMap } from '../domain/voxel-map';

// ─── Transform functions (pure math, no Three.js) ──

/**
 * Live cloud transform: cameraWorldMatrix × makeScale(S, S, -S) × point.
 * The mesh matrix combines these; for testing we apply step-by-step.
 */
function liveCloudTransform(
  camPt: { x: number; y: number; z: number },
  cameraWorldElements: ArrayLike<number>,
  scale: number,
) {
  const e = cameraWorldElements;
  const lx = camPt.x * scale;
  const ly = camPt.y * scale;
  const lz = -camPt.z * scale;
  return {
    x: e[0] * lx + e[4] * ly + e[8]  * lz + e[12],
    y: e[1] * lx + e[5] * ly + e[9]  * lz + e[13],
    z: e[2] * lx + e[6] * ly + e[10] * lz + e[14],
  };
}

function domainTransform(
  camPt: { x: number; y: number; z: number },
  cameraWorldElements: ArrayLike<number>,
  scale: number,
) {
  const worldCloud = transformCloudToWorld(
    {
      positions: new Float32Array([camPt.x, camPt.y, camPt.z]),
      colors: new Float32Array([1, 1, 1]),
      count: 1,
      timestampMs: 0,
    },
    cameraWorldElements,
    scale,
  );
  return {
    x: worldCloud.positions[0],
    y: worldCloud.positions[1],
    z: worldCloud.positions[2],
  };
}

// ─── Build test camera world matrices ────────────────

function makeCameraWorldElements(tx: number, ty: number, tz: number, yawRad: number): number[] {
  const c = Math.cos(yawRad);
  const s = Math.sin(yawRad);
  return [
    c, 0, -s, 0,
    0, 1,  0, 0,
    s, 0,  c, 0,
    tx, ty, tz, 1,
  ];
}

function makeCameraWorldElementsRPY(
  tx: number, ty: number, tz: number,
  rollRad: number, pitchRad: number, yawRad: number,
): number[] {
  const cy = Math.cos(yawRad), sy = Math.sin(yawRad);
  const cp = Math.cos(pitchRad), sp = Math.sin(pitchRad);
  const cr = Math.cos(rollRad), sr = Math.sin(rollRad);
  const r00 = cy * cr + sy * sp * sr;
  const r01 = -cy * sr + sy * sp * cr;
  const r02 = sy * cp;
  const r10 = cp * sr;
  const r11 = cp * cr;
  const r12 = -sp;
  const r20 = -sy * cr + cy * sp * sr;
  const r21 = sy * sr + cy * sp * cr;
  const r22 = cy * cp;
  return [
    r00, r10, r20, 0,
    r01, r11, r21, 0,
    r02, r12, r22, 0,
    tx, ty, tz, 1,
  ];
}

// ─── Test scenarios ──────────────────────────────────

const S = 20;

const CAMERA_POSES: { name: string; elements: number[] }[] = [
  { name: 'identity', elements: Array.from(mat4Identity()) },
  { name: 'translated', elements: makeCameraWorldElements(10, 5, -20, 0) },
  { name: 'yaw=45°', elements: makeCameraWorldElements(0, 8, -15, degToRad(45)) },
  { name: 'full RPY', elements: makeCameraWorldElementsRPY(5, 12, -8, degToRad(10), degToRad(-20), degToRad(30)) },
];

const TEST_POINTS = [
  { name: 'center 1m', camPt: { x: 0, y: 0, z: 1 } },
  { name: 'right 0.5m', camPt: { x: 0.3, y: 0, z: 0.5 } },
  { name: 'upper-left 2m', camPt: { x: -0.5, y: -0.4, z: 2 } },
  { name: 'lower-right 0.8m', camPt: { x: 0.2, y: 0.15, z: 0.8 } },
];

describe('Pipeline equivalence: expected math vs domain transform', () => {
  for (const pose of CAMERA_POSES) {
    describe(pose.name, () => {
      for (const pt of TEST_POINTS) {
        it(`point: ${pt.name}`, () => {
          const live = liveCloudTransform(pt.camPt, pose.elements, S);
          const domain = domainTransform(pt.camPt, pose.elements, S);
          expect(live.x).toBeCloseTo(domain.x, 5);
          expect(live.y).toBeCloseTo(domain.y, 5);
          expect(live.z).toBeCloseTo(domain.z, 5);
        });
      }
    });
  }
});

describe('Sanity: identity matrix preserves local scale', () => {
  it('(0.1, 0.2, 1) → (2, 4, -20)', () => {
    const I = Array.from(mat4Identity());
    const result = liveCloudTransform({ x: 0.1, y: 0.2, z: 1 }, I, S);
    expect(result.x).toBeCloseTo(2, 6);
    expect(result.y).toBeCloseTo(4, 6);
    expect(result.z).toBeCloseTo(-20, 6);
  });
});

describe('Sanity: translation offsets point', () => {
  it('camera at (10, 5, -20), origin → (10, 5, -20)', () => {
    const e = makeCameraWorldElements(10, 5, -20, 0);
    const result = liveCloudTransform({ x: 0, y: 0, z: 0 }, e, S);
    expect(result.x).toBeCloseTo(10, 6);
    expect(result.y).toBeCloseTo(5, 6);
    expect(result.z).toBeCloseTo(-20, 6);
  });
});

describe('Uniform scale preserves direction ratios', () => {
  it('scale 20 vs scale 1', () => {
    const e = Array.from(mat4Identity());
    const camPt = { x: 0.1, y: 0, z: 1 };
    const s1 = liveCloudTransform(camPt, e, 1);
    const s20 = liveCloudTransform(camPt, e, 20);
    expect(s20.x).toBeCloseTo(s1.x * 20, 6);
    expect(s20.y).toBeCloseTo(s1.y * 20, 6);
    expect(s20.z).toBeCloseTo(s1.z * 20, 6);
  });
});

describe('VoxelMap.addCloud matches liveCloudTransform', () => {
  it('identity matrix', () => {
    const vm = new VoxelMap({ voxelSizeWu: 0.1, maxVoxels: 1000 });
    const I = Array.from(mat4Identity());
    const camPt = { x: 0.5, y: 0.3, z: 2.0 };
    const worldPt = liveCloudTransform(camPt, I, S);
    const positions = new Float32Array([worldPt.x, worldPt.y, worldPt.z]);
    const colors = new Float32Array([1, 0, 0]);
    vm.addCloud(positions, colors, 1);
    const voxels = vm.getVoxels();
    expect(voxels.length).toBe(1);
    expect(voxels[0].x).toBeCloseTo(10, 1);
    expect(voxels[0].y).toBeCloseTo(6, 1);
    expect(voxels[0].z).toBeCloseTo(-40, 1);
  });

  it('rotated+translated matrix', () => {
    const vm = new VoxelMap({ voxelSizeWu: 0.01, maxVoxels: 1000 });
    const e = makeCameraWorldElementsRPY(5, 12, -8, degToRad(10), degToRad(-20), degToRad(30));
    const camPt = { x: 0.2, y: 0.15, z: 0.8 };
    const worldPt = liveCloudTransform(camPt, e, S);
    const positions = new Float32Array([worldPt.x, worldPt.y, worldPt.z]);
    const colors = new Float32Array([0, 1, 0]);
    vm.addCloud(positions, colors, 1);
    const voxels = vm.getVoxels();
    expect(voxels.length).toBe(1);
    const expected = liveCloudTransform(camPt, e, S);
    expect(voxels[0].x).toBeCloseTo(expected.x, 0);
    expect(voxels[0].y).toBeCloseTo(expected.y, 0);
    expect(voxels[0].z).toBeCloseTo(expected.z, 0);
  });
});

describe('Live data diagnostic (manual run)', () => {
  it('fetches live data and reports cloud positioning', async () => {
    if (!process.env.DIAG) {
      console.log('  [diag] Skipped. Run with DIAG=1 to see live data.');
      return;
    }

    const ROBOT_URL = 'http://localhost:8110/status';
    const DEPTH_URL = 'http://localhost:8201/api/v1/depth_camera/depth/frame_color_overlay';

    let joints: number[] = [];
    let tcpMm: number[] = [];
    let mountDeg: [number, number] = [0, 0];
    try {
      const resp = await fetch(ROBOT_URL, { signal: AbortSignal.timeout(5000) });
      const status = await resp.json();
      const data = status?.xarm?.data ?? [];
      joints = Array.isArray(data[17]) ? data[17] : [];
      tcpMm = Array.isArray(data[18]) ? data[18] : [];
      mountDeg = Array.isArray(data[51]) ? data[51] as [number, number] : [0, 0];
      console.log('\n  ┌── Robot State ──────────────────────────');
      console.log(`  │ Joints (deg):  [${joints.map(j => j.toFixed(1)).join(', ')}]`);
      console.log(`  │ TCP (mm+deg):  [${tcpMm.map(v => v.toFixed(1)).join(', ')}]`);
      console.log(`  │ Mount (tilt,rot): [${mountDeg[0]}, ${mountDeg[1]}]`);
    } catch (e) {
      console.log(`  [diag] Robot API error: ${(e as Error).message}`);
    }

    try {
      const resp = await fetch(DEPTH_URL, { signal: AbortSignal.timeout(8000) });
      const frame = await resp.json();
      const frameWidth = frame.width ?? 0;
      const frameHeight = frame.height ?? 0;
      console.log('  ├── Depth Frame ─────────────────────────');
      console.log(`  │ Resolution: ${frameWidth}×${frameHeight}`);
      console.log(`  │ Has depth: ${!!frame.depth_data}, Has color: ${!!frame.mapped_color}`);

      if (frame.depth_data) {
        const { parseBase64Uint16 } = await import('../domain/binary-parsers');
        const depthRaw = parseBase64Uint16(frame.depth_data, frameWidth * frameHeight);
        let validPx = 0;
        for (let i = 0; i < depthRaw.length; i++) {
          if (depthRaw[i] >= 50 && depthRaw[i] <= 2000) validPx++;
        }
        console.log(`  │ Valid depth pixels: ${validPx}/${depthRaw.length}`);

        const { processDepthFrame } = await import('../domain/depth-processor');
        const { DEPTH_OVERLAY, DEPTH_CAMERA } = await import('../config/scene-config');
        const cloud = processDepthFrame(
          depthRaw, null, frameWidth, frameHeight,
          DEPTH_CAMERA.intrinsics, DEPTH_CAMERA.depthCalibration, DEPTH_OVERLAY,
          DEPTH_CAMERA.depthScale,
        );

        if (cloud.count > 0) {
          let sumX = 0, sumY = 0, sumZ = 0;
          for (let i = 0; i < cloud.count; i++) {
            const off = i * 3;
            sumX += cloud.positions[off];
            sumY += cloud.positions[off + 1];
            sumZ += cloud.positions[off + 2];
          }
          console.log(`  │ Cloud points: ${cloud.count}`);
          console.log(`  │ Centroid: (${(sumX/cloud.count).toFixed(4)}, ${(sumY/cloud.count).toFixed(4)}, ${(sumZ/cloud.count).toFixed(4)}) m`);
          console.log(`  └── Cloud positioned via scene-graph cameraMountGroup.matrixWorld`);
        } else {
          console.log('  └── No valid cloud points');
        }
      }
    } catch (e) {
      console.log(`  [diag] Depth API error: ${(e as Error).message}`);
    }
  });
});
