/**
 * arm-state.ts — Types for robot arm state data.
 *
 * All angles in degrees (as received from xArm API).
 * All lift values in motor units (as received from Igus API).
 * Domain layer converts to radians/meters as needed.
 */

/** xArm model identity. */
export interface ArmIdentity {
  /** Number of axes: 5, 6, or 7.  */
  readonly axis: 5 | 6 | 7;
  /** Device type number (e.g. 6, 8, 9, 11, 12, 13). */
  readonly deviceType: number;
  /** End-effector model name (e.g. "xarm_vacuum_gripper"). */
  readonly endEffector: string;
}

/** Arm variant key (e.g. "6-6", "7-13"). Used to select ARM_SPECS. */
export type ArmVariant =
  | '5-5' | '6-6' | '6-8' | '6-9' | '6-11' | '6-12'
  | '7-7' | '7-13';

/** Joint angles from xArm API. Degrees. */
export interface JointState {
  /** Joint angles in degrees. Length = axis count. */
  readonly angles: readonly number[];
  /** Timestamp (ms epoch) when this was captured. */
  readonly timestamp: number;
}

/** Igus lift actuator state. */
export interface LiftState {
  /** Raw motor position: 0 .. 120000. */
  readonly motorUnits: number;
  /** Timestamp (ms epoch). */
  readonly timestamp: number;
}

/** Mount angles for the arm base. Degrees. */
export interface MountDegrees {
  /** Tilt angle in degrees. */
  readonly tilt: number;
  /** Rotation angle in degrees. */
  readonly rotation: number;
}

/** Complete arm snapshot — all data needed to render one frame. */
export interface ArmSnapshot {
  readonly identity: ArmIdentity;
  readonly joints: JointState;
  readonly lift: LiftState;
  readonly mount: MountDegrees;
}

/**
 * ARM_SPEC: kinematic specification for one arm variant.
 *
 * groupsPosition: pre-scale positions in meters (same as production bundle).
 * meshsRotation:  mesh Euler offsets in degrees (before adding meshRotationBase).
 *
 * jointAxes[i]: which Three.js axis joint i+1 rotates around ('X' | 'Y').
 * jointSigns[i]: +1 or -1 applied to the angle.
 * jointOffsetsDeg[i]: degree offset added before applying rotation (e.g. -180 for J1).
 */
export interface ArmSpec {
  readonly groupsPosition: ReadonlyArray<readonly [number, number, number]>;
  readonly meshsRotation: ReadonlyArray<readonly [number, number, number]>;
  readonly jointAxes: ReadonlyArray<'X' | 'Y'>;
  readonly jointSigns: ReadonlyArray<1 | -1>;
  readonly jointOffsetsDeg: ReadonlyArray<number>;
}

/** STL filename list per arm variant. */
export type StlMap = Record<ArmVariant, readonly string[]>;
