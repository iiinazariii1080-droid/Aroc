import type { AgvPose } from '@/types/transforms';
import type { StaticTransform } from '@/types/coordinates';

export interface AgvPosePolicyConfig {
  readonly anchorMode: 'agv_center' | 'arm_base';
  readonly swapXY: boolean;
  readonly invertX: boolean;
  readonly invertY: boolean;
  readonly invertYaw: boolean;
  readonly yawOffsetDeg: number;
}

export interface AgvPosePolicyResult {
  readonly remapped: {
    xM: number;
    yM: number;
    thetaDeg: number;
  };
  readonly arm: {
    xM: number;
    yM: number;
    thetaDeg: number;
  };
}

export class AgvPosePolicy {
  constructor(
    private readonly config: AgvPosePolicyConfig,
    private readonly agvToArmBase: StaticTransform,
  ) {}

  resolve(pose: AgvPose): AgvPosePolicyResult {
    let xM = pose.x_m;
    let yM = pose.y_m;
    let thetaDeg = pose.theta_deg;

    if (this.config.swapXY) {
      const temp = xM;
      xM = yM;
      yM = temp;
    }
    if (this.config.invertX) xM = -xM;
    if (this.config.invertY) yM = -yM;
    if (this.config.invertYaw) thetaDeg = -thetaDeg;
    thetaDeg += this.config.yawOffsetDeg;

    let armX = xM;
    let armY = yM;
    let armTheta = thetaDeg;

    if (this.config.anchorMode === 'arm_base') {
      const yawRad = (thetaDeg * Math.PI) / 180;
      const cos = Math.cos(yawRad);
      const sin = Math.sin(yawRad);
      armX += this.agvToArmBase.translation.x * cos - this.agvToArmBase.translation.y * sin;
      armY += this.agvToArmBase.translation.x * sin + this.agvToArmBase.translation.y * cos;
      armTheta += (this.agvToArmBase.rotation.yaw * 180) / Math.PI;
    }

    return {
      remapped: { xM, yM, thetaDeg },
      arm: { xM: armX, yM: armY, thetaDeg: armTheta },
    };
  }
}
