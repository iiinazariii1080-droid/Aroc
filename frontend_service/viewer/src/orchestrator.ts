/**
 * orchestrator.ts — Application service layer (domain orchestrator).
 *
 * Sits between data and rendering layers. Subscribes to raw data events
 * from the data layer, runs domain logic, and emits processed events
 * for the rendering layer to consume.
 *
 * Responsibilities:
 *   - Normalize raw joints from data layer
 *   - Compute FK from joints + arm spec
 *   - Process raw depth frames into point clouds
 *   - Maintain and resolve the full transform chain
 *   - Emit fk:updated and transform:resolved for rendering
 *
 * This module owns NO Three.js dependency. It depends only on:
 *   - EventBus (for communication)
 *   - Domain modules (pure math)
 *   - Config (for runtime parameters)
 */

import type { EventBus } from '@/event-bus';
import type { TransformChainState, AgvPose } from '@/types/transforms';
import type { RosPose, Mat4 } from '@/types/coordinates';
import { DEPTH_CAMERA, DEPTH_OVERLAY, VOXEL_RECORDING, TRANSFORMS, WORLD, LIFT, AGV_VISUALIZATION } from '@/config/scene-config';
import { computeFK, computeFKMatrix } from '@/domain/arm-kinematics';
import { processDepthFrame, transformCloudToWorld } from '@/domain/depth-processor';
import { TransformAuthority } from '@/domain/transform-authority';
import { Robot } from '@/domain/robot';
import { mat4Identity, degToRad } from '@/domain/coord-utils';
import { VoxelMap } from '@/domain/voxel-map';
import { validateIntrinsics, validateDepthCalibration } from '@/domain/validators';
import type { VoxelEntry, PointCloud, DepthCameraRuntimeParams } from '@/types/depth';
import type { StaticTransform } from '@/types/coordinates';
import type { RobotStatusUpdate } from '@/types/robot-status';

export interface OrchestratorOptions {
  bus: EventBus;
}

export class Orchestrator {
  private static readonly SKEW_HARD_LIMIT_MS = 80;
  private static readonly SHOT_STALE_MS = 10_000;
  private static readonly SHOT_COLOR_TIMEOUT_MS = 5_000;
  private static readonly DEFAULT_RUNTIME_FAR_M = Math.max(
    DEPTH_CAMERA.frustum.far,
    ((DEPTH_OVERLAY.maxRaw * DEPTH_CAMERA.depthCalibration.k + DEPTH_CAMERA.depthCalibration.b)
      * DEPTH_CAMERA.depthScale) / 1000 + 0.25,
  );

  private readonly bus: EventBus;
  private readonly transformAuthority: TransformAuthority;
  private readonly robot: Robot;

  // ─── Derived state (computed by orchestrator) ───
  private lastFkMatrix: Mat4 = mat4Identity();
  private lastAgvPose: RosPose = {
    position: { x: 0, y: 0, z: 0 },
    orientation: { roll: 0, pitch: 0, yaw: 0 },
  };

  // ─── Voxel map (depth cloud recording) ──────────
  private readonly voxelMap: VoxelMap;
  private recording = false;
  /** Camera world matrix elements from Three.js scene graph. */
  private cameraWorldElements: number[] | null = null;
  /** Inverse camera world matrix elements (rigid-body inverse). */
  private inverseCameraWorldElements: number[] | null = null;
  /** Runtime debug override for T_flange→camera (meters + radians). */
  private debugFlangeToCamera: StaticTransform | null = null;
  /** Last depth frame frustum tangent bounds (for clear-frame). */
  private lastFrameTanX = 0;
  private lastFrameTanY = 0;
  /** Cached last depth cloud for ui:shot (single-frame capture). */
  private lastCloud: PointCloud | null = null;
  /** Timestamp (ms) of the last cached cloud. */
  private lastCloudTimestamp = 0;
  /** Last robot pose timestamp for depth/pose skew checks. */
  private lastPoseTimestampMs = 0;
  /** Last known AGV map id from robot status. */
  private lastAgvMapId: number | null = null;
  /** One-shot flag for shot-color via single ingest pipeline. */
  private pendingShotColor = false;
  /** Timeout handle for pending shot-color request. */
  private pendingShotColorTimer: ReturnType<typeof setTimeout> | null = null;

  /** Runtime-tunable depth camera params (source of truth for cloud build). */
  private runtimeDepth: DepthCameraRuntimeParams = {
    intrinsics: {
      fx: DEPTH_CAMERA.intrinsics.fx,
      fy: DEPTH_CAMERA.intrinsics.fy,
      cx: DEPTH_CAMERA.intrinsics.cx,
      cy: DEPTH_CAMERA.intrinsics.cy,
    },
    depthScale: DEPTH_CAMERA.depthScale,
    depthCalibration: {
      k: DEPTH_CAMERA.depthCalibration.k,
      b: DEPTH_CAMERA.depthCalibration.b,
    },
    frustum: {
      near: DEPTH_CAMERA.frustum.near,
      far: Orchestrator.DEFAULT_RUNTIME_FAR_M,
    },
  };

  /** Unsubscribe functions for event listeners. */
  private readonly unsubs: (() => void)[] = [];

  constructor(opts: OrchestratorOptions) {
    this.bus = opts.bus;
    this.transformAuthority = new TransformAuthority();
    this.robot = new Robot();
    this.voxelMap = new VoxelMap({
      voxelSizeWu: VOXEL_RECORDING.voxelSizeMm / 1000 * WORLD.SCALE_FACTOR,
      maxVoxels: VOXEL_RECORDING.maxVoxels,
    });
    this.assertDepthConfig();
    this.wireEvents();
  }

  private wireEvents(): void {
    const on = <K extends import('@/event-bus').EventKey>(
      event: K,
      handler: import('@/event-bus').EventHandler<K>,
    ) => { this.unsubs.push(this.bus.on(event, handler)); };

    // ─── Unified robot status input ────────────────────────
    on('robot:status', (status) => {
      this.updateRobotStatus(status);
    });

    // ─── Raw depth frame → domain processing ────────────────
    on('raw:depthFrame', (raw) => {
      if (this.lastPoseTimestampMs > 0) {
        const skewMs = Math.abs(raw.timestamp - this.lastPoseTimestampMs);
        this.bus.emit('depth:skew', { skewMs, thresholdMs: Orchestrator.SKEW_HARD_LIMIT_MS });
        const enforceHardSkewGate = raw.timestampSource === 'source';
        if (enforceHardSkewGate && skewMs > Orchestrator.SKEW_HARD_LIMIT_MS) {
          this.bus.emit('depth:dropped', {
            reason: 'skew',
            skewMs,
            detail: `Depth/pose skew exceeded hard limit (${Orchestrator.SKEW_HARD_LIMIT_MS}ms)`,
          });
          return;
        }
      }

      // Emit the structured depth frame event
      this.bus.emit('depth:frame', {
        raw: raw.depthRaw,
        width: raw.width,
        height: raw.height,
        timestamp: raw.timestamp,
        timestampSource: raw.timestampSource,
      });

      const runtimeIntrinsics = {
        fx: this.runtimeDepth.intrinsics.fx,
        fy: this.runtimeDepth.intrinsics.fy,
        cx: this.runtimeDepth.intrinsics.cx,
        cy: this.runtimeDepth.intrinsics.cy,
        width: DEPTH_CAMERA.intrinsics.width,
        height: DEPTH_CAMERA.intrinsics.height,
      };

      const runtimeCalibration = {
        k: this.runtimeDepth.depthCalibration.k,
        b: this.runtimeDepth.depthCalibration.b,
      };

      const runtimeOverlay = {
        ...DEPTH_OVERLAY,
        minDistanceM: Math.max(DEPTH_OVERLAY.minDistanceM, this.runtimeDepth.frustum.near),
      };

      // Process into camera-local point cloud
      const cameraCloud = processDepthFrame(
        raw.depthRaw,
        raw.rgbRaw,
        raw.width,
        raw.height,
        runtimeIntrinsics,
        runtimeCalibration,
        runtimeOverlay,
        this.runtimeDepth.depthScale,
        raw.timestamp,
      );

      if (cameraCloud.count === 0) {
        this.bus.emit('depth:dropped', {
          reason: 'empty',
          detail: 'Depth frame produced no valid points after filtering',
        });
        this.bus.emit('depth:cloud', cameraCloud);
        return;
      }

      // Update frustum tangent bounds from camera-local cloud (used by clear-frame)
      let tanX = 0;
      let tanY = 0;
      for (let i = 0; i < cameraCloud.count; i++) {
        const off = i * 3;
        const x = cameraCloud.positions[off];
        const y = cameraCloud.positions[off + 1];
        const z = cameraCloud.positions[off + 2];
        const dist = z;
        if (dist <= 0.10) continue;
        tanX = Math.max(tanX, Math.abs(x) / dist);
        tanY = Math.max(tanY, Math.abs(y) / dist);
      }
      this.lastFrameTanX = tanX;
      this.lastFrameTanY = tanY;

      if (!this.cameraWorldElements) {
        this.bus.emit('depth:dropped', {
          reason: 'validation',
          detail: 'Missing camera world matrix for cloud projection',
        });
        return;
      }

      let worldCloud = transformCloudToWorld(
        cameraCloud,
        this.cameraWorldElements,
        WORLD.SCALE_FACTOR,
      );

      if (
        Number.isFinite(this.runtimeDepth.frustum.far)
        && this.runtimeDepth.frustum.far > this.runtimeDepth.frustum.near
      ) {
        worldCloud = this.clipCloudByFar(cameraCloud, worldCloud, this.runtimeDepth.frustum.far);
      }

      this.bus.emit('depth:cloud', worldCloud);
    });

    on('depth:cameraParams', (params) => {
      const nextIntrinsics = {
        fx: params.intrinsics.fx,
        fy: params.intrinsics.fy,
        cx: params.intrinsics.cx,
        cy: params.intrinsics.cy,
        width: DEPTH_CAMERA.intrinsics.width,
        height: DEPTH_CAMERA.intrinsics.height,
      };
      const intrinsicsCheck = validateIntrinsics(nextIntrinsics);
      if (!intrinsicsCheck.ok) {
        this.bus.emit('error', {
          source: 'Orchestrator',
          message: `Rejected depth params: ${intrinsicsCheck.reason}`,
        });
        return;
      }

      const nextCalibration = {
        k: params.depthCalibration.k,
        b: params.depthCalibration.b,
      };
      const calibrationCheck = validateDepthCalibration(nextCalibration, params.depthScale);
      if (!calibrationCheck.ok) {
        this.bus.emit('error', {
          source: 'Orchestrator',
          message: `Rejected depth params: ${calibrationCheck.reason}`,
        });
        return;
      }

      if (!Number.isFinite(params.frustum.near) || !Number.isFinite(params.frustum.far) || params.frustum.near <= 0 || params.frustum.far <= 0 || params.frustum.far <= params.frustum.near) {
        this.bus.emit('error', {
          source: 'Orchestrator',
          message: 'Rejected depth params: frustum near/far are invalid',
        });
        return;
      }

      this.runtimeDepth = {
        intrinsics: {
          fx: nextIntrinsics.fx,
          fy: nextIntrinsics.fy,
          cx: nextIntrinsics.cx,
          cy: nextIntrinsics.cy,
        },
        depthScale: params.depthScale,
        depthCalibration: nextCalibration,
        frustum: {
          near: params.frustum.near,
          far: params.frustum.far,
        },
      };
    });

    // ─── Voxel recording: accumulate clouds into voxel map ──
    on('depth:cloud', (cloud) => {
      // Cache last cloud for ui:shot
      if (cloud.count > 0) {
        this.lastCloud = cloud;
        this.lastCloudTimestamp = cloud.timestampMs;
      }

      if (this.pendingShotColor) {
        this.clearPendingShotColorTimer();
        this.pendingShotColor = false;
        if (cloud.count > 0) {
          this.addCloudToMap(cloud);
        }
        return;
      }

      if (!this.recording || cloud.count === 0) return;
      this.addCloudToMap(cloud);
    });

    on('ui:toggleRecording', (isOn) => {
      this.recording = isOn;
    });

    // ─── Shot: add last cached cloud to voxel map (one-frame capture) ──
    on('ui:shot', () => {
      // Guard: reject stale clouds (>10s old means polling likely stopped)
      if (
        this.lastCloud &&
        this.lastCloud.count > 0 &&
        (Date.now() - this.lastCloudTimestamp) < Orchestrator.SHOT_STALE_MS
      ) {
        this.addCloudToMap(this.lastCloud);
        return;
      }

      this.bus.emit('depth:dropped', {
        reason: 'stale',
        detail: `Shot rejected because last cloud is stale (>${Orchestrator.SHOT_STALE_MS}ms)`,
      });
    });

    // ─── Shot Color: request one immediate fetch via DepthApi, consume next cloud ──
    on('ui:shotColor', () => {
      this.clearPendingShotColorTimer();
      this.pendingShotColor = true;
      this.bus.emit('depth:fetchNow');
      this.pendingShotColorTimer = setTimeout(() => {
        if (!this.pendingShotColor) return;
        this.pendingShotColor = false;
        this.pendingShotColorTimer = null;
        this.bus.emit('depth:dropped', {
          reason: 'stale',
          detail: `Shot Color timed out after ${Orchestrator.SHOT_COLOR_TIMEOUT_MS}ms without a new frame`,
        });
      }, Orchestrator.SHOT_COLOR_TIMEOUT_MS);
    });

    on('voxel:clear', () => {
      this.voxelMap.clear();
      this.bus.emit('voxel:updated', []);
    });

    // ─── Load: replace VoxelMap with decoded voxels ─────────
    on('voxel:load', (voxels) => {
      this.voxelMap.loadVoxels(voxels);
      this.bus.emit('voxel:updated', this.voxelMap.getVoxels());
    });

    // ─── Clear-frame: frustum-based voxel erasure ───────────
    on('ui:clearFrame', () => {
      if (this.voxelMap.size === 0 || !this.inverseCameraWorldElements) return;
      const removed = this.voxelMap.clearFrustum(
        this.inverseCameraWorldElements,
        this.lastFrameTanX,
        this.lastFrameTanY,
        WORLD.SCALE_FACTOR,
      );
      if (removed > 0) {
        this.bus.emit('voxel:updated', this.voxelMap.getVoxels());
      }
    });

    // ─── Camera world matrix from Three.js scene graph ──────
    on('camera:worldMatrix', ({ elements }) => {
      this.cameraWorldElements = elements;
      // Compute rigid-body inverse inline (R^T, -R^T * t)
      const e = elements;
      this.inverseCameraWorldElements = [
        e[0], e[4], e[8],  0,
        e[1], e[5], e[9],  0,
        e[2], e[6], e[10], 0,
        -(e[0]*e[12] + e[1]*e[13] + e[2]*e[14]),
        -(e[4]*e[12] + e[5]*e[13] + e[6]*e[14]),
        -(e[8]*e[12] + e[9]*e[13] + e[10]*e[14]),
        1,
      ];
    });

    // ─── Debug camera transform override ───────────────────
    on('debug:cameraTransform', (tf) => {
      this.debugFlangeToCamera = {
        translation: {
          x: tf.tx,
          y: tf.ty,
          z: tf.tz,
        },
        rotation: {
          roll: degToRad(tf.rxDeg),
          pitch: degToRad(tf.ryDeg),
          yaw: degToRad(tf.rzDeg),
        },
        status: 'KNOWN',
      };
      this.resolveAndEmitChain();
    });

  }

  private updateRobotStatus(status: RobotStatusUpdate): void {
    const poseTs = status.rawJoints?.timestamp ?? status.rawLift?.timestamp;
    if (typeof poseTs === 'number' && Number.isFinite(poseTs)) {
      this.lastPoseTimestampMs = poseTs;
    }

    const { change, processedJoints, processedLift } = this.robot.updateStatus(status);

    // Fan-out typed events for rendering consumers
    if (change.identity) {
      this.bus.emit('arm:init', this.robot.identity);
    }
    if (change.mount) {
      this.bus.emit('arm:mount', this.robot.mount);
    }
    if (processedJoints) {
      this.bus.emit('arm:joints', processedJoints);
    }
    if (processedLift) {
      this.bus.emit('arm:lift', processedLift);
    }
    if (change.agvPose) {
      this.lastAgvPose = this.agvPoseToRosPose(this.robot.agvPose);
      this.lastAgvMapId = this.robot.agvPose.map_id;
      this.bus.emit('agv:pose', this.robot.agvPose);
    }

    // Recompute FK / chain as needed
    if (change.joints) {
      this.computeAndEmitFK();
    } else if (change.mount || change.lift || change.agvPose) {
      this.resolveAndEmitChain();
    }
  }

  // ─── FK computation ───────────────────────────────

  private computeAndEmitFK(): void {
    const fk = computeFK(this.robot.jointsDeg as number[], this.robot.spec);

    // Cumulative FK matrix: armBase → flange in ROS convention.
    // Uses the same group positions + joint angles as the Three.js scene graph,
    // then converts to ROS via COB sandwich.
    this.lastFkMatrix = computeFKMatrix(this.robot.jointsDeg as number[], this.robot.spec);

    this.bus.emit('fk:updated', {
      rotations: fk.rotations,
      fkMatrix: this.lastFkMatrix,
    });

    this.resolveAndEmitChain();
  }

  // ─── Transform chain resolution ───────────────────

  private resolveAndEmitChain(): void {
    // Build lift displacement pose (vertical in ROS frame)
    const liftM = (this.robot.liftMotorUnits / 10_000) * LIFT.metersPerTenK; // meters
    const liftPose: RosPose = {
      position: { x: 0, y: 0, z: liftM },
      orientation: { roll: 0, pitch: 0, yaw: 0 },
    };

    const state: TransformChainState = {
      worldToAgv: this.lastAgvPose,
      mountOrientation: this.robot.mount,
      agvToArmBase: TRANSFORMS.agvToArmBase,
      liftDisplacement: liftPose,
      armBaseToFlange: this.lastFkMatrix,
      flangeToCamera: this.debugFlangeToCamera ?? TRANSFORMS.flangeToCamera,
      flangeToGripper: TRANSFORMS.flangeToGripper,
    };

    const resolved = this.transformAuthority.resolve(state);
    this.bus.emit('transform:resolved', resolved);
  }

  // ─── Helpers ──────────────────────────────────────

  /** Add a depth cloud to the voxel map using current camera transform. */
  private addCloudToMap(cloud: PointCloud): void {
    const expectedMapId = AGV_VISUALIZATION.mapLayer.preferredMapId;
    if (
      Number.isFinite(expectedMapId)
      && expectedMapId > 0
      && this.lastAgvMapId != null
      && this.lastAgvMapId > 0
      && this.lastAgvMapId !== expectedMapId
    ) {
      this.bus.emit('depth:dropped', {
        reason: 'map_id_mismatch',
        detail: `AGV map_id=${this.lastAgvMapId} does not match expected map_id=${expectedMapId}`,
      });
      return;
    }

    this.voxelMap.addCloud(
      cloud.positions,
      cloud.colors,
      cloud.count,
    );
    this.bus.emit('voxel:updated', this.voxelMap.getVoxels());
  }

  private assertDepthConfig(): void {
    const intrinsicsCheck = validateIntrinsics(DEPTH_CAMERA.intrinsics);
    if (!intrinsicsCheck.ok) {
      this.bus.emit('error', {
        source: 'Orchestrator',
        message: `Invalid depth intrinsics: ${intrinsicsCheck.reason}`,
      });
    }

    const calibrationCheck = validateDepthCalibration(DEPTH_CAMERA.depthCalibration, DEPTH_CAMERA.depthScale);
    if (!calibrationCheck.ok) {
      this.bus.emit('error', {
        source: 'Orchestrator',
        message: `Invalid depth calibration: ${calibrationCheck.reason}`,
      });
    }
  }

  /** Public getter for current voxels (used by save). */
  getVoxels(): readonly VoxelEntry[] {
    return this.voxelMap.getVoxels();
  }

  /** Current voxel count. */
  get voxelCount(): number {
    return this.voxelMap.size;
  }

  private agvPoseToRosPose(agv: AgvPose): RosPose {
    return {
      position: { x: agv.x_m, y: agv.y_m, z: 0 },
      orientation: { roll: 0, pitch: 0, yaw: degToRad(agv.theta_deg) },
    };
  }

  private clearPendingShotColorTimer(): void {
    if (this.pendingShotColorTimer) {
      clearTimeout(this.pendingShotColorTimer);
      this.pendingShotColorTimer = null;
    }
  }

  private clipCloudByFar(
    cameraCloud: PointCloud,
    worldCloud: PointCloud,
    farM: number,
  ): PointCloud {
    if (cameraCloud.count === 0) return worldCloud;

    const kept: number[] = [];
    for (let i = 0; i < cameraCloud.count; i++) {
      const off = i * 3;
      const z = cameraCloud.positions[off + 2];
      if (z <= farM) kept.push(i);
    }

    if (kept.length === cameraCloud.count) return worldCloud;
    if (kept.length === 0) {
      return {
        positions: new Float32Array(0),
        colors: new Float32Array(0),
        count: 0,
        timestampMs: worldCloud.timestampMs,
      };
    }

    const positions = new Float32Array(kept.length * 3);
    const colors = new Float32Array(kept.length * 3);
    for (let i = 0; i < kept.length; i++) {
      const srcOff = kept[i] * 3;
      const dstOff = i * 3;
      positions[dstOff] = worldCloud.positions[srcOff];
      positions[dstOff + 1] = worldCloud.positions[srcOff + 1];
      positions[dstOff + 2] = worldCloud.positions[srcOff + 2];
      colors[dstOff] = worldCloud.colors[srcOff];
      colors[dstOff + 1] = worldCloud.colors[srcOff + 1];
      colors[dstOff + 2] = worldCloud.colors[srcOff + 2];
    }

    return {
      positions,
      colors,
      count: kept.length,
      timestampMs: worldCloud.timestampMs,
    };
  }

  dispose(): void {
    this.clearPendingShotColorTimer();
    for (const unsub of this.unsubs) unsub();
    this.unsubs.length = 0;
    this.voxelMap.clear();
  }
}
