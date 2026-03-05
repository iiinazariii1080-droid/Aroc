import type { ArmIdentity, JointState, LiftState, MountDegrees } from '@/types/arm-state';
import type { AgvPose } from '@/types/transforms';

export interface RawJointState {
  readonly angles: readonly number[];
  readonly timestamp: number;
}

export interface RawLiftState {
  readonly motorUnits: number;
  readonly timestamp: number;
}

export interface RobotStatusUpdate {
  readonly identity?: ArmIdentity;
  readonly mount?: MountDegrees;
  readonly rawJoints?: RawJointState;
  readonly joints?: JointState;
  readonly rawLift?: RawLiftState;
  readonly lift?: LiftState;
  readonly agvPose?: AgvPose;
}
