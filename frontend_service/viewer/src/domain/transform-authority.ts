import type { Mat4, RosPose, StaticTransform } from '@/types/coordinates';
import type { TransformChainState, ResolvedPoses } from '@/types/transforms';
import type { MountDegrees } from '@/types/arm-state';
import {
  mat4Identity,
  mat4Multiply,
  mat4FromPose,
  mat4GetTranslation,
  mat4InvertRigid,
} from '@/domain/coord-utils';

class TransformNode {
  private readonly children: TransformNode[] = [];
  private parent: TransformNode | null = null;
  private localMatrix: Mat4;
  private worldMatrix: Mat4;
  private dirty = true;

  constructor(public readonly name: string) {
    this.localMatrix = mat4Identity();
    this.worldMatrix = mat4Identity();
  }

  addChild(child: TransformNode): TransformNode {
    child.parent = this;
    this.children.push(child);
    child.markDirty();
    return child;
  }

  setLocalMatrix(matrix: Mat4): void {
    this.localMatrix = matrix;
    this.markDirty();
  }

  setLocalPose(pose: RosPose): void {
    this.setLocalMatrix(mat4FromPose(
      pose.position.x,
      pose.position.y,
      pose.position.z,
      pose.orientation.roll,
      pose.orientation.pitch,
      pose.orientation.yaw,
    ));
  }

  setLocalStaticTransform(tf: StaticTransform): void {
    this.setLocalMatrix(mat4FromPose(
      tf.translation.x,
      tf.translation.y,
      tf.translation.z,
      tf.rotation.roll,
      tf.rotation.pitch,
      tf.rotation.yaw,
    ));
  }

  getWorldMatrix(): Mat4 {
    if (this.dirty) {
      if (this.parent) {
        this.worldMatrix = mat4Multiply(this.parent.getWorldMatrix(), this.localMatrix);
      } else {
        this.worldMatrix = this.localMatrix;
      }
      this.dirty = false;
    }
    return this.worldMatrix;
  }

  private markDirty(): void {
    if (this.dirty) return;
    this.dirty = true;
    for (const child of this.children) {
      child.markDirty();
    }
  }
}

function matrixToPosePosition(matrix: Mat4): RosPose {
  return {
    position: mat4GetTranslation(matrix),
    orientation: { roll: 0, pitch: 0, yaw: 0 },
  };
}

export class TransformAuthority {
  private readonly world = new TransformNode('world');
  private readonly agv = this.world.addChild(new TransformNode('agv'));
  private readonly mount = this.agv.addChild(new TransformNode('mount'));
  private readonly armBase = this.mount.addChild(new TransformNode('armBase'));
  private readonly lift = this.armBase.addChild(new TransformNode('lift'));
  private readonly flange = this.lift.addChild(new TransformNode('flange'));
  private readonly camera = this.flange.addChild(new TransformNode('camera'));
  private readonly gripper = this.flange.addChild(new TransformNode('gripper'));

  resolve(state: TransformChainState): ResolvedPoses {
    this.world.setLocalMatrix(mat4Identity());
    this.agv.setLocalPose(state.worldToAgv);
    this.mount.setLocalPose(this.mountDegreesToPose(state.mountOrientation));
    this.armBase.setLocalStaticTransform(state.agvToArmBase);
    this.lift.setLocalPose(state.liftDisplacement);
    this.flange.setLocalMatrix(state.armBaseToFlange);
    this.camera.setLocalStaticTransform(state.flangeToCamera);
    this.gripper.setLocalStaticTransform(state.flangeToGripper);

    const armBaseWorld = this.armBase.getWorldMatrix();
    const flangeWorld = this.flange.getWorldMatrix();
    const cameraWorld = this.camera.getWorldMatrix();

    return {
      armBase: matrixToPosePosition(armBaseWorld),
      tcp: matrixToPosePosition(flangeWorld),
      camera: matrixToPosePosition(cameraWorld),
      worldToCamera: mat4InvertRigid(cameraWorld),
    };
  }

  /**
   * Convert mount degrees to a RosPose:
   *   tilt  → pitch (ry) — rotation around Y axis
   *   rotation → roll (rx) — rotation around X axis
   */
  private mountDegreesToPose(mount: MountDegrees): RosPose {
    const DEG2RAD = Math.PI / 180;
    return {
      position: { x: 0, y: 0, z: 0 },
      orientation: {
        roll: mount.rotation * DEG2RAD,   // rx
        pitch: mount.tilt * DEG2RAD,      // ry
        yaw: 0,
      },
    };
  }
}
