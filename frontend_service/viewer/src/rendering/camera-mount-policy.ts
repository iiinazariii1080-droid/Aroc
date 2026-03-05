import type { StaticTransform } from '@/types/coordinates';
import type * as THREE from 'three';

export interface CameraDebugTransform {
  tx: number;
  ty: number;
  tz: number;
  rxDeg: number;
  ryDeg: number;
  rzDeg: number;
}

export interface CameraMountPose {
  px: number;
  py: number;
  pz: number;
  rx: number;
  ry: number;
  rz: number;
}

export class CameraMountPolicy {
  constructor(private readonly worldScale: number) {}

  fromFlangeToCamera(tf: StaticTransform): CameraMountPose {
    return {
      px: -tf.translation.y * this.worldScale,
      py: tf.translation.z * this.worldScale,
      pz: -tf.translation.x * this.worldScale,
      rx: tf.rotation.roll,
      ry: tf.rotation.yaw,
      rz: tf.rotation.pitch,
    };
  }

  fromDebugTransform(tf: CameraDebugTransform): CameraMountPose {
    return {
      px: -tf.ty * this.worldScale,
      py: tf.tz * this.worldScale,
      pz: -tf.tx * this.worldScale,
      rx: (tf.rxDeg * Math.PI) / 180,
      ry: (tf.rzDeg * Math.PI) / 180,
      rz: (tf.ryDeg * Math.PI) / 180,
    };
  }

  applyToObject(
    obj: Pick<THREE.Object3D, 'position' | 'rotation'>,
    pose: CameraMountPose,
  ): void {
    obj.position.set(pose.px, pose.py, pose.pz);
    obj.rotation.set(pose.rx, pose.ry, pose.rz, 'XYZ');
  }
}
