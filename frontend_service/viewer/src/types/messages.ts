/**
 * messages.ts — Typed postMessage protocol between host page and viewer iframe.
 *
 * Based on: static/docs/arm3d-iframe-contract.md
 */

// ─── Host → Viewer ───

export interface ArmInitPayload {
  readonly axis: number;
  readonly type: number;
  readonly mountDegrees: readonly [number, number];
  readonly endEffector: string;
}

export interface ArmBootstrapPayload {
  readonly axis: number;
  readonly type: number;
  readonly joints: readonly number[];
  readonly mountDegrees: readonly [number, number];
  readonly lift: number;
  readonly timestamp: number;
  readonly endEffector: string;
  readonly seq?: number;
}

export interface ArmStatePayload {
  readonly joints: readonly number[];
  readonly mountDegrees?: readonly [number, number];
  readonly lift: number;
  readonly timestamp: number;
}

export interface ArmConfigPayload {
  readonly fallbackPolling?: boolean;
  readonly ignoreMount?: boolean;
}

export type HostToViewerMessage =
  | { readonly type: 'arm3d:bootstrap'; readonly payload: ArmBootstrapPayload }
  | { readonly type: 'arm3d:init'; readonly payload: ArmInitPayload }
  | { readonly type: 'arm3d:state'; readonly payload: ArmStatePayload }
  | { readonly type: 'arm3d:config'; readonly payload: ArmConfigPayload };

// ─── Viewer → Host ───

export interface ViewerReadyPayload {
  readonly version: number;
  readonly capabilities: readonly string[];
}

export interface BootstrapAckPayload {
  readonly seq?: number;
  readonly timestamp: number;
}

export type ViewerToHostMessage =
  | { readonly type: 'arm3d:ready'; readonly payload: ViewerReadyPayload }
  | { readonly type: 'arm3d:bootstrapAck'; readonly payload: BootstrapAckPayload }
  | { readonly type: 'arm3d:requestSnapshot'; readonly payload: Record<string, never> };

// ─── Union ───

export type Arm3dMessage = HostToViewerMessage | ViewerToHostMessage;

/**
 * Type guard: is this a valid arm3d message?
 */
export function isArm3dMessage(data: unknown): data is Arm3dMessage {
  if (typeof data !== 'object' || data === null) return false;
  const msg = data as Record<string, unknown>;
  return typeof msg.type === 'string' && msg.type.startsWith('arm3d:');
}
