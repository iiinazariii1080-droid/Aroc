/**
 * message-bridge.ts — postMessage bridge between host page and viewer iframe.
 *
 * Data layer. Translates postMessage events into typed EventBus events.
 * Also sends viewer→host messages (ready, requestSnapshot).
 */

import type { EventBus } from '@/event-bus';
import type {
  HostToViewerMessage,
  ArmInitPayload,
  ArmStatePayload,
  ArmConfigPayload,
  ArmBootstrapPayload,
} from '@/types/messages';
import { isArm3dMessage } from '@/types/messages';
import { isTraceEnabled, trace } from '@/utils/trace';

export interface MessageBridgeOptions {
  bus: EventBus;
  /** Parent window to send messages to. Defaults to window.parent. */
  parentWindow?: Window;
  /** Target origin for outgoing messages. Defaults to '*'. */
  targetOrigin?: string;
}

export class MessageBridge {
  private readonly bus: EventBus;
  private readonly parentWindow: Window;
  private readonly targetOrigin: string;
  private disposed = false;
  private readonly traceOn: boolean;
  private messageSeq = 0;
  private lastMountKey = '';

  constructor(opts: MessageBridgeOptions) {
    this.bus = opts.bus;
    this.parentWindow = opts.parentWindow ?? window.parent;
    this.targetOrigin = opts.targetOrigin ?? '*';
    this.traceOn = isTraceEnabled();

    window.addEventListener('message', this.onMessage);
  }

  private summarize(payload: unknown): unknown {
    if (!payload || typeof payload !== 'object') {
      return payload;
    }
    const p = payload as Record<string, unknown>;
    const out: Record<string, unknown> = { ...p };
    const joints = p.joints;
    if (Array.isArray(joints)) {
      out.jointsCount = joints.length;
      out.jointsPreview = joints.slice(0, 6);
    }
    return out;
  }

  private traceRx(type: string, payload: unknown): void {
    if (!this.traceOn) return;
    this.messageSeq += 1;
    trace('bridge', 'RX', {
      seq: this.messageSeq,
      type,
      payload: this.summarize(payload),
    });
  }

  private traceTx(type: string, payload: unknown): void {
    if (!this.traceOn) return;
    this.messageSeq += 1;
    trace('bridge', 'TX', {
      seq: this.messageSeq,
      type,
      payload: this.summarize(payload),
    });
  }

  // ─── Incoming messages ────────────────────────

  private onMessage = (event: MessageEvent): void => {
    if (this.disposed) return;
    const data = event.data;
    if (!isArm3dMessage(data)) return;

    const msg = data as HostToViewerMessage;
    this.traceRx(msg.type, (msg as { payload?: unknown }).payload);

    switch (msg.type) {
      case 'arm3d:bootstrap':
        this.handleBootstrap(msg.payload);
        break;
      case 'arm3d:init':
        this.handleInit(msg.payload);
        break;
      case 'arm3d:state':
        this.handleState(msg.payload);
        break;
      case 'arm3d:config':
        this.handleConfig(msg.payload);
        break;
    }
  };

  private handleBootstrap(payload: ArmBootstrapPayload): void {
    this.traceRx('arm3d:bootstrap:apply', payload);
    this.handleInit({
      axis: payload.axis,
      type: payload.type,
      mountDegrees: payload.mountDegrees,
      endEffector: payload.endEffector,
    });

    this.handleState({
      joints: payload.joints,
      mountDegrees: payload.mountDegrees,
      lift: payload.lift,
      timestamp: payload.timestamp,
    });

    const ackPayload = {
      seq: payload.seq,
      timestamp: Date.now(),
    };
    this.traceTx('arm3d:bootstrapAck', ackPayload);
    this.parentWindow.postMessage(
      {
        type: 'arm3d:bootstrapAck',
        payload: ackPayload,
      },
      this.targetOrigin,
    );
  }

  private handleInit(payload: ArmInitPayload): void {
    // Validate axis
    const axis = payload.axis;
    if (axis !== 5 && axis !== 6 && axis !== 7) {
      this.bus.emit('error', {
        source: 'MessageBridge',
        message: `Invalid axis value: ${axis}. Expected 5, 6, or 7.`,
      });
      return;
    }

    const mount = this.pickMountIfChanged(payload.mountDegrees);

    this.bus.emit('robot:status', {
      identity: {
        axis,
        deviceType: payload.type,
        endEffector: payload.endEffector || '',
      },
      mount,
    });
  }

  private handleState(payload: ArmStatePayload): void {
    const timestamp = payload.timestamp || Date.now();
    const mount = this.pickMountIfChanged(payload.mountDegrees);
    this.bus.emit('robot:status', {
      joints: payload.joints
        ? {
            angles: payload.joints,
            timestamp,
          }
        : undefined,
      mount,
      lift: payload.lift !== undefined
        ? {
            motorUnits: payload.lift,
            timestamp,
          }
        : undefined,
    });
  }

  private handleConfig(payload: ArmConfigPayload): void {
    this.bus.emit('arm:config', payload);
  }

  private pickMountIfChanged(mountDegrees: readonly [number, number] | undefined): { tilt: number; rotation: number } | undefined {
    if (!Array.isArray(mountDegrees) || mountDegrees.length < 2) {
      return undefined;
    }
    const tilt = mountDegrees[0] ?? 0;
    const rotation = mountDegrees[1] ?? 0;
    const mountKey = `${Number(tilt).toFixed(3)}:${Number(rotation).toFixed(3)}`;
    if (mountKey === this.lastMountKey) {
      return undefined;
    }
    this.lastMountKey = mountKey;
    return { tilt, rotation };
  }

  // ─── Outgoing messages ────────────────────────

  sendReady(): void {
    const payload = { version: 2, capabilities: ['depth', 'voxel', 'agv'] };
    this.traceTx('arm3d:ready', payload);
    this.parentWindow.postMessage(
      { type: 'arm3d:ready', payload },
      this.targetOrigin,
    );
  }

  sendRequestSnapshot(): void {
    this.traceTx('arm3d:requestSnapshot', {});
    this.parentWindow.postMessage(
      { type: 'arm3d:requestSnapshot', payload: {} },
      this.targetOrigin,
    );
  }

  // ─── Dispose ──────────────────────────────────

  dispose(): void {
    this.disposed = true;
    window.removeEventListener('message', this.onMessage);
  }
}
