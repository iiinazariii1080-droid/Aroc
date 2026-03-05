/**
 * event-bus.ts — Typed publish/subscribe event bus.
 *
 * Central communication backbone. All cross-layer data flows through events.
 * Zero dependencies. Fully synchronous dispatch.
 *
 * Usage:
 *   const bus = createEventBus();
 *   const unsub = bus.on('arm:snapshot', (snap) => { ... });
 *   bus.emit('arm:snapshot', snapshot);
 *   unsub();                     // unsubscribe
 *   bus.off('arm:snapshot');      // remove ALL listeners for this event
 *   bus.dispose();                // remove ALL listeners for ALL events
 */

import type { ArmSnapshot, ArmIdentity, JointState, LiftState, MountDegrees } from '@/types/arm-state';
import type { AgvPose, ResolvedPoses } from '@/types/transforms';
import type { PointCloud, DepthFrame, VoxelEntry, FrameDropReason, DepthCameraRuntimeParams } from '@/types/depth';
import type { ArmConfigPayload } from '@/types/messages';
import type { Mat4 } from '@/types/coordinates';
import type { RobotStatusUpdate } from '@/types/robot-status';

// ─── Event map ───────────────────────────────────

export interface EventMap {
  // Arm
  'arm:init':       ArmIdentity;
  'arm:snapshot':   ArmSnapshot;
  'arm:joints':     JointState;
  'arm:lift':       LiftState;
  'arm:mount':      MountDegrees;
  'arm:config':     ArmConfigPayload;
  'robot:status':   RobotStatusUpdate;

  // Raw data events (emitted by data layer before domain processing)
  'raw:joints':     { angles: readonly number[]; timestamp: number };
  'raw:lift':       { motorUnits: number; timestamp: number };
  'raw:depthFrame': {
    depthRaw: Uint16Array;
    rgbRaw: Uint8Array | null;
    width: number;
    height: number;
    timestamp: number;
    timestampSource: 'source' | 'local';
  };

  // AGV
  'agv:pose':       AgvPose;

  // Transforms
  'fk:updated':     { rotations: readonly import('@/domain/arm-kinematics').JointRotation[]; fkMatrix: Mat4 };
  'transform:resolved': ResolvedPoses;

  // Camera world matrix (Three.js scene graph, column-major 16 floats)
  'camera:worldMatrix': { elements: number[] };

  // Depth camera
  'depth:fetchNow': undefined;
  'depth:frame':    DepthFrame;
  'depth:cloud':    PointCloud;
  'depth:skew':     { skewMs: number; thresholdMs: number };
  'depth:dropped':  { reason: FrameDropReason; detail?: string; skewMs?: number };

  // Voxel map
  'voxel:updated':  readonly VoxelEntry[];
  'voxel:clear':    undefined;

  // UI commands
  'ui:toggleDepth':       boolean;
  'ui:toggleAxes':        boolean;
  'ui:toggleFrustum':     boolean;
  'ui:toggleRecording':   boolean;
  'ui:clearFrame':        undefined;
  'ui:pointSize':         number;
  'ui:shot':              undefined;
  'ui:shotColor':         undefined;
  'debug:cameraTransform': {
    tx: number;
    ty: number;
    tz: number;
    rxDeg: number;
    ryDeg: number;
    rzDeg: number;
  };
  'depth:cameraParams': DepthCameraRuntimeParams;

  // Voxel persistence
  'voxel:load':           VoxelEntry[];

  // Viewer lifecycle
  'viewer:ready':   { version: number };
  'viewer:resize':  { width: number; height: number };
  'viewer:dispose': undefined;

  // Error
  'error':          { source: string; message: string; detail?: unknown };
}

// ─── Types ───────────────────────────────────────

export type EventKey = keyof EventMap;
export type EventHandler<K extends EventKey> = (payload: EventMap[K]) => void;
type Unsubscribe = () => void;

// ─── Implementation ──────────────────────────────

export interface EventBus {
  /** Subscribe to an event. Returns unsubscribe function. */
  on<K extends EventKey>(event: K, handler: EventHandler<K>): Unsubscribe;

  /** Subscribe to an event, auto-unsubscribe after first call. */
  once<K extends EventKey>(event: K, handler: EventHandler<K>): Unsubscribe;

  /** Emit an event synchronously to all subscribers. */
  emit<K extends EventKey>(event: K, ...args: EventMap[K] extends undefined ? [] : [EventMap[K]]): void;

  /** Remove ALL handlers for a specific event. */
  off<K extends EventKey>(event: K): void;

  /** Remove ALL handlers for ALL events. */
  dispose(): void;

  /** Number of listeners for a given event (for debugging). */
  listenerCount<K extends EventKey>(event: K): number;
}

export function createEventBus(): EventBus {
  const listeners = new Map<EventKey, Set<EventHandler<any>>>();

  function getSet(event: EventKey): Set<EventHandler<any>> {
    let s = listeners.get(event);
    if (!s) {
      s = new Set();
      listeners.set(event, s);
    }
    return s;
  }

  const bus: EventBus = {
    on<K extends EventKey>(event: K, handler: EventHandler<K>): Unsubscribe {
      const set = getSet(event);
      set.add(handler);
      return () => { set.delete(handler); };
    },

    once<K extends EventKey>(event: K, handler: EventHandler<K>): Unsubscribe {
      const wrapper: EventHandler<K> = (payload) => {
        set.delete(wrapper);
        handler(payload);
      };
      const set = getSet(event);
      set.add(wrapper);
      return () => { set.delete(wrapper); };
    },

    emit<K extends EventKey>(event: K, ...args: EventMap[K] extends undefined ? [] : [EventMap[K]]): void {
      const set = listeners.get(event);
      if (!set) return;
      const payload = args[0] as EventMap[K];
      // Snapshot the set to avoid mutation during iteration
      for (const handler of [...set]) {
        try {
          handler(payload);
        } catch (err) {
          console.error(`[EventBus] Error in handler for "${event}":`, err);
        }
      }
    },

    off<K extends EventKey>(event: K): void {
      listeners.delete(event);
    },

    dispose(): void {
      listeners.clear();
    },

    listenerCount<K extends EventKey>(event: K): number {
      return listeners.get(event)?.size ?? 0;
    },
  };

  return bus;
}
