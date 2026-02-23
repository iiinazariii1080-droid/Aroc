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
import type { AgvPose, ResolvedPoses, TransformChainState } from '@/types/transforms';
import type { PointCloud, DepthFrame, RgbFrame, VoxelEntry } from '@/types/depth';
import type { ArmConfigPayload } from '@/types/messages';

// ─── Event map ───────────────────────────────────

export interface EventMap {
  // Arm
  'arm:init':       ArmIdentity;
  'arm:snapshot':   ArmSnapshot;
  'arm:joints':     JointState;
  'arm:lift':       LiftState;
  'arm:mount':      MountDegrees;
  'arm:config':     ArmConfigPayload;

  // AGV
  'agv:pose':       AgvPose;

  // Transforms
  'fk:updated':     TransformChainState;
  'transform:resolved': ResolvedPoses;

  // Depth camera
  'depth:frame':    DepthFrame;
  'depth:rgb':      RgbFrame;
  'depth:cloud':    PointCloud;

  // Voxel map
  'voxel:updated':  readonly VoxelEntry[];
  'voxel:clear':    void;

  // UI commands
  'ui:toggleDepth':       boolean;
  'ui:toggleAxes':        boolean;
  'ui:toggleFrustum':     boolean;
  'ui:toggleRecording':   boolean;
  'ui:requestSnapshot':   void;

  // Viewer lifecycle
  'viewer:ready':   { version: number };
  'viewer:resize':  { width: number; height: number };
  'viewer:dispose': void;

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
  emit<K extends EventKey>(event: K, payload: EventMap[K]): void;

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

    emit<K extends EventKey>(event: K, payload: EventMap[K]): void {
      const set = listeners.get(event);
      if (!set) return;
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
