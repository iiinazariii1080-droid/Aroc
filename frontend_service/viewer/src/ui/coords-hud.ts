/**
 * coords-hud.ts — TCP coordinates HUD overlay.
 *
 * UI layer. Shows real-time TCP position (ROS mm), joint angles,
 * lift status, and AGV pose in a fixed overlay.
 *
 * Activated via `?coords` query param.
 */

import type { EventBus } from '@/event-bus';
import { threeToRos, worldToMm, DEFAULT_SCALE_FACTOR } from '@/domain/coord-utils';
import { liftDisplacementMeters, type LiftConfig } from '@/domain/lift-model';

// ─── Styles ────────────────────────────────────────

const HUD_CSS = [
  'position:fixed',
  'left:12px',
  'top:12px',
  'padding:8px 12px',
  'border-radius:6px',
  'background:rgba(0,0,0,0.6)',
  'color:#a5f3c4',
  'font-family:monospace',
  'font-size:12px',
  'line-height:1.5',
  'z-index:12',
  'user-select:none',
  'pointer-events:none',
  'white-space:pre',
].join(';');

// ─── Component ─────────────────────────────────────

export interface CoordsHudOptions {
  bus: EventBus;
  liftCfg: LiftConfig;
}

export class CoordsHud {
  private el: HTMLDivElement | null = null;
  private readonly bus: EventBus;
  private readonly liftCfg: LiftConfig;

  private lastJointsDeg: number[] = [];
  private lastLiftMU = 0;
  private lastTcpWu: { x: number; y: number; z: number } | null = null;
  private lastAgvPose: { xM: number; yM: number; thetaDeg: number; mapId: number } | null = null;

  constructor(opts: CoordsHudOptions) {
    this.bus = opts.bus;
    this.liftCfg = opts.liftCfg;

    // Create HUD element
    this.el = document.createElement('div');
    this.el.id = 'arm3d-coords-hud';
    this.el.style.cssText = HUD_CSS;
    this.el.textContent = 'TCP: waiting...';
    document.body.appendChild(this.el);

    // Subscribe to events
    this.bus.on('arm:joints', (joints) => {
      this.lastJointsDeg = joints.angles.slice();
    });

    this.bus.on('arm:lift', (lift) => {
      this.lastLiftMU = lift.motorUnits;
    });

    this.bus.on('agv:pose', (pose) => {
      this.lastAgvPose = { xM: pose.x_m, yM: pose.y_m, thetaDeg: pose.theta_deg, mapId: pose.map_id };
    });
  }

  /**
   * Called per-frame from the render loop with the current TCP world position.
   * Pass the Three.js world position of the tool group.
   */
  updateTcp(tcpWorldX: number, tcpWorldY: number, tcpWorldZ: number): void {
    this.lastTcpWu = { x: tcpWorldX, y: tcpWorldY, z: tcpWorldZ };
    this.render();
  }

  private render(): void {
    if (!this.el) return;
    const tcp = this.lastTcpWu;
    if (!tcp) {
      this.el.textContent = 'TCP: no data';
      return;
    }

    // Convert Three.js world units → ROS mm
    const ros = threeToRos(tcp);
    const rxMm = worldToMm(ros.x * DEFAULT_SCALE_FACTOR);
    const ryMm = worldToMm(ros.y * DEFAULT_SCALE_FACTOR);
    const rzMm = worldToMm(ros.z * DEFAULT_SCALE_FACTOR);

    let text = `TCP (ROS mm): ${rxMm.toFixed(1)}, ${ryMm.toFixed(1)}, ${rzMm.toFixed(1)}`;
    text += `\nTCP (wu):  ${tcp.x.toFixed(2)}, ${tcp.y.toFixed(2)}, ${tcp.z.toFixed(2)}`;

    if (this.lastLiftMU > 0) {
      const liftM = liftDisplacementMeters(this.lastLiftMU, this.liftCfg);
      text += `\nLift: ${this.lastLiftMU} mU = ${(liftM * 1000).toFixed(1)} mm`;
    }

    if (this.lastJointsDeg.length > 0) {
      text += `\nJ: ${this.lastJointsDeg.map((j) => (j || 0).toFixed(1)).join(', ')}`;
    }

    if (this.lastAgvPose) {
      const ap = this.lastAgvPose;
      text += `\nAGV (ROS m): ${ap.xM.toFixed(3)}, ${ap.yM.toFixed(3)}`;
      text += `  Yaw: ${ap.thetaDeg.toFixed(2)}°  map: ${ap.mapId}`;
    }

    this.el.textContent = text;
  }

  dispose(): void {
    if (this.el?.parentNode) {
      this.el.parentNode.removeChild(this.el);
    }
    this.el = null;
  }
}
