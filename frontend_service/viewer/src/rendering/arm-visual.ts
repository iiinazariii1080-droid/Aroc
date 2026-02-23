/**
 * arm-visual.ts — Three.js arm mesh builder and joint updater.
 *
 * Rendering layer. Builds the scene graph from ArmSpec / GroupSetupData,
 * loads STL meshes, and applies FK results from the domain layer.
 */

import * as THREE from 'three';
import { STLLoader } from 'three/addons/loaders/STLLoader.js';
import type { ArmVariant } from '@/types/arm-state';
import type { MountDegrees } from '@/types/arm-state';
import type { EventBus } from '@/event-bus';
import { ARM_SPECS, getStlList, resolveVariant } from '@/config/arm-specs';
import { ROBOT_ARM } from '@/config/scene-config';
import { computeFK, computeGroupSetup, type GroupSetupData } from '@/domain/arm-kinematics';
import { liftObliqueDisplacement_threeWu, group1AbsoluteY_threeWu } from '@/domain/lift-model';
import { degToRad } from '@/domain/coord-utils';

export class ArmVisual {
  private readonly scene: THREE.Scene;
  private readonly bus: EventBus;

  /** Root group: all arm geometry lives under this. */
  readonly rootGroup: THREE.Group;

  /** Per-joint groups (index 0 = base carrier, 1..n = joints, last = tool). */
  private modelGroups: THREE.Group[] = [];

  /** Tool group — everything attached to the final flange. */
  private toolGroup: THREE.Group | null = null;

  /** Lift visual node (moves with lift). */
  private liftVisualNode: THREE.Group | null = null;

  /** Current variant. */
  private variant: ArmVariant = '6-6';

  /** STL loader instance. */
  private readonly stlLoader: STLLoader;

  /** Debug: transform delta from localStorage. */
  private debugRotation = [0, 0, 0];
  private debugPosition = [0, 0, 0];

  constructor(scene: THREE.Scene, bus: EventBus) {
    this.scene = scene;
    this.bus = bus;
    this.rootGroup = new THREE.Group();
    this.rootGroup.name = 'arm-root';
    this.scene.add(this.rootGroup);
    this.stlLoader = new STLLoader();

    // Wire events
    this.bus.on('arm:init', (identity) => {
      this.rebuild(identity.axis, identity.deviceType);
    });

    this.bus.on('arm:snapshot', (snap) => {
      this.applyJoints(snap.joints.angles);
      this.applyLift(snap.lift.motorUnits);
      this.applyMount(snap.mount);
    });

    this.bus.on('arm:joints', (joints) => {
      this.applyJoints(joints.angles);
    });

    this.bus.on('arm:lift', (lift) => {
      this.applyLift(lift.motorUnits);
    });

    this.bus.on('arm:mount', (mount) => {
      this.applyMount(mount);
    });
  }

  // ─── Build / Rebuild ──────────────────────────

  rebuild(axis: number, deviceType: number): void {
    // Clear previous
    while (this.rootGroup.children.length > 0) {
      const child = this.rootGroup.children[0];
      this.rootGroup.remove(child);
    }
    this.modelGroups = [];
    this.toolGroup = null;
    this.liftVisualNode = null;

    this.variant = resolveVariant(axis, deviceType);
    const spec = ARM_SPECS[this.variant];
    const setup = computeGroupSetup(spec, [...ROBOT_ARM.meshRotationBaseDeg]);

    // Build group hierarchy
    const groups: THREE.Group[] = [];
    for (let i = 0; i < setup.length; i++) {
      const g = new THREE.Group();
      g.name = `joint-group-${i}`;
      const [px, py, pz] = setup[i].position;
      g.position.set(px, py, pz);

      if (i === 0) {
        this.rootGroup.add(g);
      } else {
        groups[i - 1].add(g);
      }
      groups.push(g);
    }

    this.modelGroups = groups;

    // Create lift visual node (attaches to groups[0])
    if (groups.length > 1) {
      this.liftVisualNode = groups[0];
    }

    // Identify tool group (last group)
    if (groups.length > 0) {
      this.toolGroup = groups[groups.length - 1];
    }

    // Load STL meshes
    this.loadMeshes(setup);
  }

  private loadMeshes(setup: GroupSetupData[]): void {
    const stlList = getStlList(
      parseInt(this.variant.split('-')[0]),
      parseInt(this.variant.split('-')[1]),
    );

    const material = new THREE.MeshPhongMaterial({
      color: 0xe8e8e8,
      specular: 0x333333,
      shininess: 30,
    });

    for (let i = 0; i < stlList.length && i < this.modelGroups.length; i++) {
      const url = `/static/stl/${stlList[i]}`;
      const groupIdx = i;
      const [rx, ry, rz] = setup[i].meshRotationDeg;

      this.stlLoader.load(url, (geometry: THREE.BufferGeometry) => {
        geometry.computeVertexNormals();
        const mesh = new THREE.Mesh(geometry, material.clone());
        mesh.name = `link-${groupIdx}`;
        mesh.scale.set(20, 20, 20);
        mesh.rotation.set(degToRad(rx), degToRad(ry), degToRad(rz));
        mesh.castShadow = true;
        mesh.receiveShadow = true;

        if (this.modelGroups[groupIdx]) {
          this.modelGroups[groupIdx].add(mesh);
        }
      });
    }
  }

  // ─── Joint update ─────────────────────────────

  applyJoints(anglesDeg: readonly number[]): void {
    const spec = ARM_SPECS[this.variant];
    const fk = computeFK(anglesDeg, spec);

    for (const rot of fk.rotations) {
      const group = this.modelGroups[rot.groupIndex];
      if (!group) continue;

      // Reset rotation, then apply the FK angle on the correct axis
      // We only set the specific axis; preserve other rotation components
      if (rot.axis === 'x') {
        group.rotation.x = rot.angle;
      } else {
        group.rotation.y = rot.angle;
      }
    }
  }

  // ─── Lift update ──────────────────────────────

  applyLift(motorUnits: number): void {
    if (this.modelGroups.length < 2) return;
    const g1 = this.modelGroups[1];
    if (!g1) return;

    const disp = liftObliqueDisplacement_threeWu(motorUnits);

    // Update lift visual node (groups[0] children position)
    if (this.liftVisualNode) {
      this.liftVisualNode.position.y = disp.dy;
      this.liftVisualNode.position.x = disp.dx;
      this.liftVisualNode.position.z = disp.dz;
    }

    // groups[1] position: base + lift displacement
    g1.position.y = group1AbsoluteY_threeWu(motorUnits);
    g1.position.x = disp.dx;
    g1.position.z = disp.dz;
  }

  // ─── Mount update ─────────────────────────────

  applyMount(mount: MountDegrees): void {
    if (this.modelGroups.length === 0) return;
    const g0 = this.modelGroups[0];
    if (!g0) return;

    const tiltDeg = mount.tilt;
    const rotateDeg = mount.rotation - 90; // legacy offset

    // Compute quaternion rotation (proper rigid body)
    const axisZ = new THREE.Vector3(0, 0, 1).normalize();
    const axisMountTilted = new THREE.Vector3(
      -Math.sin(degToRad(tiltDeg)),
      Math.cos(degToRad(tiltDeg)),
      0,
    ).normalize();

    const qTilt = new THREE.Quaternion().setFromAxisAngle(axisZ, degToRad(tiltDeg));
    const qRotate = new THREE.Quaternion().setFromAxisAngle(axisMountTilted, degToRad(rotateDeg));
    const qFinal = new THREE.Quaternion().multiplyQuaternions(qRotate, qTilt);

    // Apply debug delta if any
    const debugEuler = new THREE.Euler(
      degToRad(this.debugRotation[0]),
      degToRad(this.debugRotation[1]),
      degToRad(this.debugRotation[2]),
      'XYZ',
    );
    const qDebug = new THREE.Quaternion().setFromEuler(debugEuler);
    qFinal.multiply(qDebug);

    g0.setRotationFromQuaternion(qFinal);

    // Legacy position offsets
    const posX = (tiltDeg <= 90) ? (5 / 90) * tiltDeg : (5 / 90) * (180 - tiltDeg);
    const posY = (6 / 180) * tiltDeg - 5;
    g0.position.set(
      posX + this.debugPosition[0],
      posY + this.debugPosition[1],
      this.debugPosition[2],
    );
  }

  // ─── Debug controls ───────────────────────────

  setDebugRotation(rx: number, ry: number, rz: number): void {
    this.debugRotation = [rx, ry, rz];
  }

  setDebugPosition(px: number, py: number, pz: number): void {
    this.debugPosition = [px, py, pz];
  }

  // ─── Getters ──────────────────────────────────

  getToolGroup(): THREE.Group | null {
    return this.toolGroup;
  }

  getVariant(): ArmVariant {
    return this.variant;
  }

  dispose(): void {
    this.rootGroup.traverse((obj) => {
      if (obj instanceof THREE.Mesh) {
        obj.geometry?.dispose();
        if (Array.isArray(obj.material)) {
          obj.material.forEach(m => m.dispose());
        } else {
          obj.material?.dispose();
        }
      }
    });
    this.scene.remove(this.rootGroup);
  }
}
