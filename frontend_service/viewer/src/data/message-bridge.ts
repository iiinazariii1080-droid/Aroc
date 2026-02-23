/**
 * message-bridge.ts — postMessage bridge between host page and viewer iframe.
 *
 * Data layer. Translates postMessage events into typed EventBus events.
 * Also sends viewer→host messages (ready, requestSnapshot).
 */

import type { EventBus } from '@/event-bus';
import type { HostToViewerMessage, ArmInitPayload, ArmStatePayload, ArmConfigPayload } from '@/types/messages';
import { isArm3dMessage } from '@/types/messages';

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

  constructor(opts: MessageBridgeOptions) {
    this.bus = opts.bus;
    this.parentWindow = opts.parentWindow ?? window.parent;
    this.targetOrigin = opts.targetOrigin ?? '*';

    window.addEventListener('message', this.onMessage);
  }

  // ─── Incoming messages ────────────────────────

  private onMessage = (event: MessageEvent): void => {
    if (this.disposed) return;
    const data = event.data;
    if (!isArm3dMessage(data)) return;

    const msg = data as HostToViewerMessage;

    switch (msg.type) {
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

  private handleInit(payload: ArmInitPayload): void {
    // Emit arm identity
    this.bus.emit('arm:init', {
      axis: payload.axis as 5 | 6 | 7,
      deviceType: payload.type,
      endEffector: payload.endEffector || '',
    });

    // Emit mount degrees
    if (payload.mountDegrees) {
      this.bus.emit('arm:mount', {
        tilt: payload.mountDegrees[0] ?? 0,
        rotation: payload.mountDegrees[1] ?? 0,
      });
    }
  }

  private handleState(payload: ArmStatePayload): void {
    // Emit joints
    if (payload.joints) {
      this.bus.emit('arm:joints', {
        angles: payload.joints,
        timestamp: payload.timestamp || Date.now(),
      });
    }

    // Emit lift
    if (payload.lift !== undefined) {
      this.bus.emit('arm:lift', {
        motorUnits: payload.lift,
        timestamp: payload.timestamp || Date.now(),
      });
    }
  }

  private handleConfig(payload: ArmConfigPayload): void {
    this.bus.emit('arm:config', payload);
  }

  // ─── Outgoing messages ────────────────────────

  sendReady(): void {
    this.parentWindow.postMessage(
      { type: 'arm3d:ready', payload: { version: 2, capabilities: ['depth', 'voxel', 'agv'] } },
      this.targetOrigin,
    );
  }

  sendRequestSnapshot(): void {
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
