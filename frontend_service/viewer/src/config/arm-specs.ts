/**
 * arm-specs.ts — Kinematic specifications for each arm variant.
 *
 * Migrated from ARM_SPECS in legacy arm3d-runtime.js.
 * Pure data — no Three.js dependency.
 *
 * groupsPosition: pre-scale positions in meters.
 * meshsRotation:  mesh Euler offsets in degrees (before adding meshRotationBaseDeg).
 * jointAxes[i]:   rotation axis for joint i+1 in Three.js frame.
 * jointSigns[i]:  sign multiplier for joint angle.
 * jointOffsetsDeg[i]: degree offset before rotation (e.g. -180 for J1).
 */

import type { ArmSpec, ArmVariant, StlMap } from '@/types/arm-state';

export const ARM_SPECS: Record<ArmVariant, ArmSpec> = {
  '5-5': {
    groupsPosition: [
      [0, -0.25, 0], [0, 0.267, 0], [0, 0, 0],
      [0, 0.285, -0.0535], [0, -0.3425, -0.0775], [0, -0.097, -0.076],
    ],
    meshsRotation: [
      [0, 0, 0], [0, 0, 180], [0, -90, 180],
      [0, -90, 180], [0, -90, 180], [0, 180, 180],
    ],
    jointAxes: ['Y', 'X', 'X', 'X', 'Y'],
    jointSigns: [1, -1, -1, -1, -1],
    jointOffsetsDeg: [-180, 0, 0, 0, 0],
  },

  '6-6': {
    groupsPosition: [
      [0, -0.25, 0], [0, 0.267, 0], [0, 0, 0],
      [0, 0.285, -0.0535], [0, -0.3425, -0.0775], [0, 0, 0], [0, -0.097, -0.076],
    ],
    meshsRotation: [
      [0, 0, 0], [0, 0, 180], [0, -90, 180],
      [0, -90, 180], [180, 0, 0], [0, -90, 180], [0, 180, 180],
    ],
    jointAxes: ['Y', 'X', 'X', 'Y', 'X', 'Y'],
    jointSigns: [1, -1, -1, -1, -1, -1],
    jointOffsetsDeg: [-180, 0, 0, 0, 0, 0],
  },

  '6-8': {
    groupsPosition: [
      [0, -0.25, 0], [0, 0.267, 0], [0, 0, 0],
      [0, 0.418, -0.0535], [0, -0.4655, -0.0775], [0, 0.002, 0], [0, -0.095, -0.076],
    ],
    meshsRotation: [
      [0, 0, 0], [0, 0, 180], [0, -90, 180],
      [0, 90, 180], [180, 0, 0], [0, -90, 180], [0, 180, 180],
    ],
    jointAxes: ['Y', 'X', 'X', 'Y', 'X', 'Y'],
    jointSigns: [1, -1, 1, -1, -1, -1],
    jointOffsetsDeg: [-180, 0, 0, 0, 0, 0],
  },

  '6-9': {
    groupsPosition: [
      [0, -0.25, 0], [0, 0.2435, 0], [0, 0, 0],
      [0, 0.2002, 0], [0, -0.22761, -0.087], [0, 0, 0], [0, -0.0625, 0],
    ],
    meshsRotation: [
      [0, 0, 0], [0, 0, 180], [0, -90, 90],
      [0, 90, 180], [180, 0, 0], [0, -90, 180], [0, 180, 180],
    ],
    jointAxes: ['Y', 'X', 'X', 'Y', 'X', 'Y'],
    jointSigns: [1, -1, 1, -1, -1, -1],
    jointOffsetsDeg: [-180, 0, 0, 0, 0, 0],
  },

  '6-11': {
    groupsPosition: [
      [0, -0.25, 0], [0, 0.267, 0], [0, 0, 0],
      [0, 0.445, -0.0535], [0, -0.3425, -0.0775], [0, 0, 0], [0, -0.097, -0.076],
    ],
    meshsRotation: [
      [0, 0, 0], [0, 0, 180], [0, -90, 180],
      [0, 90, 180], [180, 0, 0], [0, -90, 180], [0, 180, 180],
    ],
    jointAxes: ['Y', 'X', 'X', 'Y', 'X', 'Y'],
    jointSigns: [1, -1, 1, 1, -1, -1],
    jointOffsetsDeg: [-180, 0, 0, 0, 0, 0],
  },

  '6-12': {
    groupsPosition: [
      [0, -0.25, 0], [0, 0.364, 0], [0, 0, 0],
      [0, 0.39, 0], [0, -0.426, -0.15], [0, 0, 0], [0, -0.09, 0],
    ],
    meshsRotation: [
      [0, 0, 0], [0, 0, 180], [180, -90, 0],
      [0, -90, 180], [0, 180, 180], [0, -90, 180], [0, 180, 180],
    ],
    jointAxes: ['Y', 'X', 'X', 'Y', 'X', 'Y'],
    jointSigns: [1, 1, -1, -1, 1, -1],
    jointOffsetsDeg: [-180, 0, 0, 0, 0, 0],
  },

  '7-7': {
    groupsPosition: [
      [0, -0.25, 0], [0, 0.267, 0], [0, 0, 0],
      [0, 0.293, 0], [0, 0, -0.0525], [0, -0.3425, -0.0775], [0, 0, 0], [0, -0.097, -0.076],
    ],
    meshsRotation: [
      [0, 0, 0], [0, 0, 180], [0, -90, 180],
      [0, 0, 180], [0, 90, 180], [180, 0, 0], [0, -90, 180], [0, 180, 180],
    ],
    jointAxes: ['Y', 'X', 'Y', 'X', 'Y', 'X', 'Y'],
    jointSigns: [1, -1, 1, 1, -1, -1, -1],
    jointOffsetsDeg: [-180, 0, 0, 0, 0, 0, 0],
  },

  '7-13': {
    groupsPosition: [
      [0, -0.25, 0], [0, 0.266, 0], [0, 0, 0],
      [0, 0.292, 0], [0, 0, -0.0525], [0, -0.3425, -0.0775], [0, 0, 0], [0, -0.097, -0.076],
    ],
    meshsRotation: [
      [0, 0, 0], [0, 0, 180], [0, 90, 180],
      [0, 0, 180], [0, -90, 180], [180, 0, 0], [0, 90, 180], [0, 180, 180],
    ],
    jointAxes: ['Y', 'X', 'Y', 'X', 'Y', 'X', 'Y'],
    jointSigns: [1, 1, 1, -1, -1, 1, -1],
    jointOffsetsDeg: [-180, 0, 0, 0, 0, 0, 0],
  },
};


/** Fallback variant when exact axis-type combo not found. */
export const VARIANT_FALLBACK: Record<number, ArmVariant> = {
  5: '5-5',
  6: '6-6',
  7: '7-7',
};


/** STL filename map per variant. */
export const STL_MAP: StlMap = {
  '5-5': ['link0.a0b8702.stl', 'link1.65c358c.stl', 'link2.7567d0d.stl', 'link3.2a2a7bf.stl', 'link4.57d6fea.stl', 'link5.12b891c.stl'],
  '6-6': ['link0.1de18b9.stl', 'link1.fc9e972.stl', 'link2.f35afe6.stl', 'link3.7c98c07.stl', 'link4.effe135.stl', 'link5.165f228.stl', 'link6.12b891c.stl'],
  '6-8': ['link0.1de18b9.stl', 'link1.fc9e972.stl', 'link2.1ef6377.stl', 'link3.4a6fa41.stl', 'link4.6387be2.stl', 'link5.165f228.stl', 'link6.12b891c.stl'],
  '6-9': ['link0.32eb957.stl', 'link1.96908fd.stl', 'link2.3e9c070.stl', 'link3.5e0ba78.stl', 'link4.bdad0a0.stl', 'link5.e57804e.stl', 'link6.28e8751.stl'],
  '6-11': ['link0.1de18b9.stl', 'link1.fc9e972.stl', 'link2.9a98a9d.stl', 'link3.ccdf24d.stl', 'link4.effe135.stl', 'link5.165f228.stl', 'link6.12b891c.stl'],
  '6-12': ['link0.f86babb.stl', 'link1.996b105.stl', 'link2.e8af0f9.stl', 'link3.be5eefa.stl', 'link4.d50a217.stl', 'link5.244fafb.stl', 'link6.cecc499.stl'],
  '7-7': ['link0.d7f039f.stl', 'link1.bc742cf.stl', 'link2.eeae964.stl', 'link3.79cfa8a.stl', 'link4.2222f0d.stl', 'link5.bce977e.stl', 'link6.83ff53c.stl', 'link7.c30a9fc.stl'],
  '7-13': ['link0.def507c.stl', 'link1.f67a75b.stl', 'link2.06d3f89.stl', 'link3.43af14d.stl', 'link4.932ff1a.stl', 'link5.396c117.stl', 'link6.6ed2841.stl', 'link7.b47a9c7.stl'],
};


/** Resolve variant key from axis + device type numbers. */
export function resolveVariant(axis: number, deviceType: number): ArmVariant {
  const key = `${axis}-${deviceType}` as ArmVariant;
  if (key in ARM_SPECS) return key;
  const fallback = VARIANT_FALLBACK[axis];
  if (fallback) return fallback;
  return '6-6';
}

/** Get ArmSpec for given axis + device type. */
export function getArmSpec(axis: number, deviceType: number): ArmSpec {
  return ARM_SPECS[resolveVariant(axis, deviceType)];
}

/** Get STL list for given axis + device type. */
export function getStlList(axis: number, deviceType: number): readonly string[] {
  return STL_MAP[resolveVariant(axis, deviceType)];
}
