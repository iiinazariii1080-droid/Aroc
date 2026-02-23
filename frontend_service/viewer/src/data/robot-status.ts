/**
 * robot-status.ts — Fallback polling for robot status when postMessage is unavailable.
 *
 * Data layer. Polls /api/v1/robot/status and /api/v1/xarm/joints_position
 * to get arm state when the iframe host doesn't push state via postMessage.
 */

import type { EventBus } from '@/event-bus';
import { API } from '@/config/scene-config';
import { normalizeJoints } from '@/domain/validators';

export interface RobotStatusPollerOptions {
  bus: EventBus;
  axisCount: number;
  pollIntervalMs?: number;
}

export class RobotStatusPoller {
  private readonly bus: EventBus;
  private axisCount: number;
  private timer: ReturnType<typeof setInterval> | null = null;
  private inFlight = false;
  private disposed = false;

  constructor(opts: RobotStatusPollerOptions) {
    this.bus = opts.bus;
    this.axisCount = opts.axisCount;

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

  setAxisCount(count: number): void {
    this.axisCount = count;
  }

  private async fetchOnce(): Promise<void> {
    if (this.inFlight || this.disposed) return;
    this.inFlight = true;

    try {
      // Fetch joints
      const jointsResp = await fetch(API.xarmJoints);
      if (jointsResp.ok) {
        const data = await jointsResp.json();
        const rawAngles = data.joints ?? data.angles ?? data;
        if (Array.isArray(rawAngles)) {
          const angles = normalizeJoints(rawAngles, this.axisCount);
          this.bus.emit('arm:joints', {
            angles,
            timestamp: Date.now(),
          });
        }
      }

      // Fetch lift/status
      const statusResp = await fetch(API.robotStatus);
      if (statusResp.ok) {
        const status = await statusResp.json();
        if (typeof status.lift === 'number') {
          this.bus.emit('arm:lift', {
            motorUnits: status.lift,
            timestamp: Date.now(),
          });
        }
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

  dispose(): void {
    this.disposed = true;
    this.stop();
  }
}
