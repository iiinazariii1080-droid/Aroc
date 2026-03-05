/**
 * robot-status.ts — Fallback polling for robot status when postMessage is unavailable.
 *
 * Data layer. Polls the unified /api/v1/robot/status endpoint to get
 * arm joints, lift position, AGV pose, and arm identity — all from a
 * single request (matching the legacy polling behavior).
 * Emits raw data events; domain processing is handled by the orchestrator.
 */

import type { EventBus } from '@/event-bus';
import { API } from '@/config/scene-config';
import type { RobotStatusUpdate } from '@/types/robot-status';

export interface RobotStatusPollerOptions {
  bus: EventBus;
  pollIntervalMs?: number;
}

export class RobotStatusPoller {
  private readonly bus: EventBus;
  private timer: ReturnType<typeof setInterval> | null = null;
  private inFlight = false;
  private disposed = false;
  /** Track last arm identity to avoid repeated re-init. */
  private lastAxis = 0;
  private lastDeviceType = 0;
  private lastMountKey = '';
  private startupMountReady = false;
  private mountMissingWarned = false;

  constructor(opts: RobotStatusPollerOptions) {
    this.bus = opts.bus;

    if (opts.pollIntervalMs && opts.pollIntervalMs > 0) {
      this.start(opts.pollIntervalMs);
    }
  }

  start(intervalMs: number): void {
    this.stop();
    this.fetchOnce();
    this.timer = setInterval(() => this.fetchOnce(), intervalMs);
  }

  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  private async fetchOnce(): Promise<void> {
    if (this.inFlight || this.disposed) return;
    this.inFlight = true;

    try {
      const resp = await fetch(API.robotStatus, { cache: 'no-store' });
      if (!resp.ok) return;
      const payload = await resp.json();

      const xarm = payload.xarm ?? {};
      const summary = xarm.summary ?? {};

      // ─── Arm identity (emit first to avoid FK/rebuild race) ─────────
      const axisRaw = payload.xarm_axis ?? summary.xarm_axis ?? xarm.xarm_axis;
      const typeRaw = payload.xarm_device_type ?? summary.xarm_device_type ?? xarm.xarm_device_type;
      let axisCandidate = Number(axisRaw);
      let typeCandidate = Number(typeRaw);

      // ─── Joints (resolved early, emitted later after init/mount checks) ─────
      const rawJoints =
        payload.joints
        ?? xarm.joints
        ?? summary.joints
        ?? xarm.angles
        ?? summary.angles
        ?? this.pickJointsFromXarmData(xarm.data)
        ?? null;

      // If the API doesn't expose axis info, infer from joint count
      if (!Number.isFinite(axisCandidate) && Array.isArray(rawJoints)) {
        axisCandidate = rawJoints.length >= 7 ? 6 : rawJoints.length >= 5 ? rawJoints.length as number : 6;
      }
      if (!Number.isFinite(typeCandidate)) {
        typeCandidate = axisCandidate;
      }

      let identityUpdate: RobotStatusUpdate['identity'] | undefined;
      if (
        Number.isFinite(axisCandidate)
        && Number.isFinite(typeCandidate)
        && (axisCandidate !== this.lastAxis || typeCandidate !== this.lastDeviceType)
      ) {
        this.lastAxis = axisCandidate;
        this.lastDeviceType = typeCandidate;
        const axis = ([5, 6, 7].includes(axisCandidate) ? axisCandidate : 6) as 5 | 6 | 7;
        identityUpdate = {
          axis,
          deviceType: typeCandidate,
          endEffector: 'xarm_vacuum_gripper',
        };
      }

      // ─── Mount degrees (tilt, rotate) ────────────────────
      const mountFromData = this.pickMountFromXarmData(xarm.data);
      const rawMount =
        payload.xarm_mount_degrees
        ?? summary.xarm_mount_degrees
        ?? xarm.xarm_mount_degrees
        ?? mountFromData
        ?? null;
      let mountUpdate: RobotStatusUpdate['mount'] | undefined;
      if (Array.isArray(rawMount) && rawMount.length >= 2) {
        const tilt = Number(rawMount[0]);
        const rotation = Number(rawMount[1]);
        if (Number.isFinite(tilt) && Number.isFinite(rotation)
          && Math.abs(tilt) <= 180
          && Math.abs(rotation) <= 360) {
          const mountKey = `${tilt.toFixed(3)}:${rotation.toFixed(3)}`;
          const changed = mountKey !== this.lastMountKey;
          if (changed) {
            console.info('[arm3d] mount resolved', {
              tilt,
              rotation,
              source: this.mountSource(payload, summary, xarm, mountFromData),
            });
            this.lastMountKey = mountKey;
            mountUpdate = {
              tilt,
              rotation,
            };
          }
          if (changed || !this.startupMountReady) {
            this.startupMountReady = true;
            this.mountMissingWarned = false;
          }
        }
      }

      // Strict startup gate: until mount is known, skip first kinematic emits.
      // Prevents crooked initial pose in fallback mode due to missing mount.
      let rawJointsUpdate: RobotStatusUpdate['rawJoints'];
      let rawLiftUpdate: RobotStatusUpdate['rawLift'];

      if (!this.startupMountReady) {
        if (!this.mountMissingWarned) {
          this.mountMissingWarned = true;
          console.warn('[arm3d] fallback startup waiting for mount degrees; joints/lift are temporarily gated');
        }
      } else {
        const timestamp = Date.now();
        if (Array.isArray(rawJoints)) {
          rawJointsUpdate = {
            angles: rawJoints.map(Number),
            timestamp,
          };
        }

        // ─── Lift (igus) ─────────────────────────────
        const igus = payload.igus ?? {};
        const liftPos = igus.position;
        if (liftPos !== undefined && liftPos !== null) {
          rawLiftUpdate = {
            motorUnits: Number(liftPos) || 0,
            timestamp,
          };
        }
      }

      // ─── AGV pose ────────────────────────────────
      let agvPoseUpdate: RobotStatusUpdate['agvPose'];
      const symovoPose = payload.symovo?.pose;
      if (symovoPose) {
        const xM = Number(symovoPose.x_m);
        const yM = Number(symovoPose.y_m);
        const thetaDeg = Number(symovoPose.theta_deg);
        const mapId = Number(symovoPose.map_id) || 0;
        if (Number.isFinite(xM) && Number.isFinite(yM) && Number.isFinite(thetaDeg)) {
          agvPoseUpdate = {
            x_m: xM,
            y_m: yM,
            theta_deg: thetaDeg,
            map_id: mapId,
          };
        }
      }

      const statusUpdate: RobotStatusUpdate = {
        ...(identityUpdate ? { identity: identityUpdate } : {}),
        ...(mountUpdate ? { mount: mountUpdate } : {}),
        ...(rawJointsUpdate ? { rawJoints: rawJointsUpdate } : {}),
        ...(rawLiftUpdate ? { rawLift: rawLiftUpdate } : {}),
        ...(agvPoseUpdate ? { agvPose: agvPoseUpdate } : {}),
      };

      if (
        statusUpdate.identity
        || statusUpdate.mount
        || statusUpdate.rawJoints
        || statusUpdate.rawLift
        || statusUpdate.agvPose
      ) {
        this.bus.emit('robot:status', statusUpdate);
      }
    } catch (err) {
      this.bus.emit('error', {
        source: 'RobotStatusPoller',
        message: 'Failed to poll robot status',
        detail: err,
      });
    } finally {
      this.inFlight = false;
    }
  }

  /**
   * Legacy xArm data array extraction: try positions [19], [18],
   * then scan for a plausible joint array (6–8 finite values ≤720°).
   */
  private pickJointsFromXarmData(data: unknown): number[] | null {
    if (!Array.isArray(data)) return null;

    for (const idx of [19, 18]) {
      const entry = data[idx];
      if (Array.isArray(entry) && entry.length >= 6) return entry;
    }

    for (const entry of data) {
      if (!Array.isArray(entry) || entry.length < 6 || entry.length > 8) continue;
      const nums = entry.map(Number);
      if (nums.some((v: number) => !Number.isFinite(v))) continue;
      if (nums.some((v: number) => Math.abs(v) > 720)) continue;
      if (nums.every((v: number) => Math.abs(v) < 1e-6)) continue;
      return nums;
    }

    return null;
  }

  /**
   * Extract mount degrees [tilt, rotate] from legacy xarm.data arrays.
   * Known placement in current payload: index 51.
   */
  private pickMountFromXarmData(data: unknown): [number, number] | null {
    if (!Array.isArray(data)) return null;

    const direct = data[51];
    if (Array.isArray(direct) && direct.length >= 2) {
      const tilt = Number(direct[0]);
      const rotation = Number(direct[1]);
      if (Number.isFinite(tilt) && Number.isFinite(rotation)) {
        return [tilt, rotation];
      }
    }

    return null;
  }

  private mountSource(
    payload: any,
    summary: any,
    xarm: any,
    mountFromData: [number, number] | null,
  ): string {
    if (Array.isArray(payload?.xarm_mount_degrees)) return 'payload.xarm_mount_degrees';
    if (Array.isArray(summary?.xarm_mount_degrees)) return 'summary.xarm_mount_degrees';
    if (Array.isArray(xarm?.xarm_mount_degrees)) return 'xarm.xarm_mount_degrees';
    if (mountFromData) return 'xarm.data[51]';
    return 'unknown';
  }

  dispose(): void {
    this.disposed = true;
    this.stop();
  }
}
