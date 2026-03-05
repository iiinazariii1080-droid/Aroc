import * as THREE from 'three';
import type { MountDegrees } from '@/types/arm-state';
import { ARM_CANONICAL_VISUAL } from '@/config/scene-config';
import { degToRad } from '@/domain/coord-utils';

export class RootTransformPolicy {
  /**
   * Apply root transform to the arm root group.
   * Mount tilt → RX, mount rotation → RY (Euler XYZ degrees).
   * These values come directly from xarm status data and define
   * the physical mounting orientation of the arm on the AGV.
   */
  applyRootTransform(
    rootGroup: THREE.Group,
    mount: MountDegrees,
    rootDebugRotation: readonly [number, number, number],
    rootDebugPosition: readonly [number, number, number],
  ): void {
    rootGroup.rotation.set(
      degToRad(mount.tilt + rootDebugRotation[0]),
      degToRad(mount.rotation + rootDebugRotation[1]),
      degToRad(rootDebugRotation[2]),
      'XYZ',
    );
    rootGroup.position.set(
      ARM_CANONICAL_VISUAL.positionWu[0] + rootDebugPosition[0],
      ARM_CANONICAL_VISUAL.positionWu[1] + rootDebugPosition[1],
      ARM_CANONICAL_VISUAL.positionWu[2] + rootDebugPosition[2],
    );
  }
}
