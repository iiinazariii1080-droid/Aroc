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
import { ROBOT_ARM, LIFT, WORLD, TRANSFORMS } from '@/config/scene-config';
import { computeGroupSetup, type GroupSetupData } from '@/domain/arm-kinematics';
import { liftObliqueDisplacement_threeWu, group1AbsoluteY_threeWu, type LiftConfig } from '@/domain/lift-model';
import { degToRad } from '@/domain/coord-utils';
import { isTraceEnabled, trace } from '@/utils/trace';
import { RootTransformPolicy } from '@/rendering/root-transform-policy';
import { CameraMountPolicy } from '@/rendering/camera-mount-policy';

// ─── Materials (shared) ─────────────────────────

const MAT_LINK = () => new THREE.MeshPhongMaterial({
  color: 0xffffff, specular: 0x333333, shininess: 40,
});

const MAT_BASE = () => new THREE.MeshPhongMaterial({
  color: 0xcccccc, specular: 0x222222, shininess: 30,
});

const MAT_TOOL = () => new THREE.MeshPhongMaterial({
  color: 0xf59e0b, specular: 0x444444, shininess: 40,
});

const MAT_JOINT_SPHERE = () => new THREE.MeshPhongMaterial({
  color: 0x60a5fa, specular: 0x555555, shininess: 50, transparent: true, opacity: 0.7,
});

export class ArmVisual {
  private readonly scene: THREE.Scene;
  private readonly bus: EventBus;

  /** Root group: all arm geometry lives under this. */
  readonly rootGroup: THREE.Group;

  /** Per-joint groups (index 0 = base carrier, 1..n = joints, last = tool). */
  private modelGroups: THREE.Group[] = [];

  /** Tool group — everything attached to the final flange. */
  private toolGroup: THREE.Group | null = null;

  /** Invisible camera-mount group (child of toolGroup) for reading camera world matrix. */
  private cameraMountGroup: THREE.Group | null = null;

  /** Policy for positioning camera mount from flangeToCamera transform. */
  private readonly cameraMountPolicy = new CameraMountPolicy(WORLD.SCALE_FACTOR);

  /** Base plate group (visual only, not kinematic). */
  private baseGroupNode: THREE.Group | null = null;

  /** Lift visual node (moves with lift). */
  private liftVisualNode: THREE.Object3D | null = null;

  /** Current variant. */
  private variant: ArmVariant = '6-6';

  /** STL loader instance. */
  private readonly stlLoader: STLLoader;

  /** Lift configuration from scene-config. */
  private readonly liftCfg: LiftConfig;

  /** Debug transform for the whole model root group. */
  private rootDebugRotation = [0, 0, 0];
  private rootDebugPosition = [0, 0, 0];

  /** Lift interpolation state. */
  private liftTarget = 0;
  private liftCurrent = 0;
  private liftInitialized = false;
  private static readonly LIFT_LERP_FACTOR = 0.25;
  private static readonly LIFT_SNAP_THRESHOLD = 0.02;

  /** When true, mount rotation messages are ignored. */
  private ignoreMount = false;

  /** When false, STL meshes are not loaded (sphere placeholders only). */
  private readonly useStl: boolean;

  /** Rebuild generation token to ignore stale async STL callbacks. */
  private rebuildGeneration = 0;

  /** Last known mount, reapplied after rebuild for deterministic startup. */
  private lastMount: MountDegrees = { tilt: 0, rotation: 0 };
  private mountInitialized = false;
  private lastAppliedMountKey = '';
  private readonly traceOn: boolean;
  private readonly rootTransformPolicy: RootTransformPolicy;

  constructor(scene: THREE.Scene, bus: EventBus, opts?: { useStl?: boolean }) {
    this.scene = scene;
    this.bus = bus;
    this.useStl = opts?.useStl ?? true;
    this.rootGroup = new THREE.Group();
    this.rootGroup.name = 'arm-root';
    this.scene.add(this.rootGroup);
    this.stlLoader = new STLLoader();
    this.traceOn = isTraceEnabled();
    this.rootTransformPolicy = new RootTransformPolicy();
    this.liftCfg = {
      worldUnitsPerTenK: LIFT.worldUnitsPerTenK,
      directionPerL_threeYup: LIFT.directionPerL_threeYup,
      group1BaseY_wu: LIFT.group1BaseY_wu,
      positionLimits: LIFT.positionLimits,
      scaleFactor: WORLD.SCALE_FACTOR,
    };

    // Wire events
    this.bus.on('arm:init', (identity) => {
      this.logTrace('arm:init', identity);
      this.rebuild(identity.axis, identity.deviceType);
    });

    this.bus.on('arm:snapshot', (snap) => {
      this.logTrace('arm:snapshot', {
        jointsCount: snap.joints.angles.length,
        lift: snap.lift.motorUnits,
        mount: snap.mount,
      });
      // Snapshots set lift immediately (no interpolation)
      this.liftTarget = snap.lift.motorUnits;
      this.liftCurrent = snap.lift.motorUnits;
      this.liftInitialized = true;
      this.applyLift(snap.lift.motorUnits);
      if (!this.ignoreMount) this.applyMount(snap.mount);
    });

    this.bus.on('arm:joints', (_joints) => {
      // Joint rotations are now applied via fk:updated from the orchestrator.
    });

    this.bus.on('fk:updated', (fk) => {
      this.logTrace('fk:updated', { rotations: fk.rotations.length });
      this.applyFKRotations(fk.rotations);
    });

    this.bus.on('arm:lift', (lift) => {
      this.logTrace('arm:lift', { motorUnits: lift.motorUnits, ts: lift.timestamp });
      this.liftTarget = lift.motorUnits;
      if (!this.liftInitialized) {
        this.liftCurrent = lift.motorUnits;
        this.liftInitialized = true;
        this.applyLift(lift.motorUnits);
      }
      // Otherwise, interpolation happens in update()
    });

    this.bus.on('arm:mount', (mount) => {
      this.logTrace('arm:mount', mount);
      this.lastMount = { tilt: mount.tilt, rotation: mount.rotation };
      this.mountInitialized = true;
      const mountKey = `${Number(mount.tilt).toFixed(3)}:${Number(mount.rotation).toFixed(3)}`;
      if (mountKey === this.lastAppliedMountKey) {
        this.logTrace('arm:mount:skip-unchanged', { mountKey });
        return;
      }
      this.lastAppliedMountKey = mountKey;
      if (!this.ignoreMount) this.applyMount(mount);
    });

    // Debug camera transform override: re-position cameraMountGroup
    this.bus.on('debug:cameraTransform', (tf) => {
      if (!this.cameraMountGroup) return;
      const debugPose = this.cameraMountPolicy.fromDebugTransform(tf);
      this.cameraMountPolicy.applyToObject(this.cameraMountGroup, debugPose);
      this.cameraMountGroup.updateMatrix();
      this.emitCameraWorldMatrix();
    });
  }

  private logTrace(message: string, data?: unknown): void {
    if (!this.traceOn) return;
    trace('arm-visual', message, data);
  }

  // ─── Build / Rebuild ──────────────────────────

  rebuild(axis: number, deviceType: number): void {
    this.rebuildGeneration += 1;
    const generation = this.rebuildGeneration;
    this.logTrace('rebuild:start', { axis, deviceType, generation });

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

    // liftVisualNode: the mesh/sphere inside groups[0] that visually
    // represents the lift column. We move this mesh (not the group itself)
    // so children (groups[1]..N) are not double-displaced.
    // Placeholder sphere is named 'jball_0'; later replaced by STL mesh
    // (loadLinkStl sets liftVisualNode to the new mesh).
    this.liftVisualNode = null;

    // Identify tool group (last group)
    if (groups.length > 0) {
      this.toolGroup = groups[groups.length - 1];
    }

    // Create invisible camera mount group (always present, even without debug params)
    this.createCameraMountGroup();

    // Load STL meshes
    this.loadMeshes(setup, generation);

    // Deterministic rehydrate of already-known startup state.
    if (this.mountInitialized && !this.ignoreMount) {
      this.applyMount(this.lastMount);
    }
    if (this.liftInitialized) {
      this.applyLift(this.liftCurrent);
    } else if (this.liftTarget !== 0) {
      this.applyLift(this.liftTarget);
    }

    this.applyRootDebugTransform();
    this.logTrace('rebuild:done', {
      generation,
      variant: this.variant,
      groups: this.modelGroups.length,
      mountInitialized: this.mountInitialized,
      liftInitialized: this.liftInitialized,
    });
  }

  private loadMeshes(setup: GroupSetupData[], generation: number): void {
    const stlList = getStlList(
      parseInt(this.variant.split('-')[0]),
      parseInt(this.variant.split('-')[1]),
    );

    // Placeholder joint spheres (replaced once STL loads)
    for (let i = 0; i < this.modelGroups.length; i++) {
      const sphere = new THREE.Mesh(
        new THREE.SphereGeometry(1.2, 12, 8),
        MAT_JOINT_SPHERE(),
      );
      sphere.name = `jball_${i}`;
      this.modelGroups[i].add(sphere);
      // Link-0 sphere is the lift visual placeholder
      if (i === 0) this.liftVisualNode = sphere;
    }

    // Skip STL loading if stl=0
    if (!this.useStl) return;

    // Load link STLs (replace spheres on success)
    for (let i = 0; i < stlList.length && i < this.modelGroups.length; i++) {
      this.loadLinkStl(i, stlList[i], setup[i].meshRotationDeg, generation);
    }

    // Base plate STL
    this.loadBaseStl(generation);

    // Tool STL (vacuum gripper)
    this.loadToolStl(generation);
  }

  /** Load a single link STL, replacing the placeholder sphere. */
  private loadLinkStl(idx: number, fname: string, rotDeg: readonly number[], generation: number): void {
    const url = `/static/stl/${fname}`;
    this.logTrace('stl:load:start', { idx, url, generation });
    this.stlLoader.load(url, (geometry: THREE.BufferGeometry) => {
      if (generation !== this.rebuildGeneration) {
        this.logTrace('stl:load:stale', { idx, generation, currentGeneration: this.rebuildGeneration });
        return;
      }

      geometry.computeVertexNormals();
      const mesh = new THREE.Mesh(geometry, MAT_LINK());
      mesh.name = `link-${idx}`;
      mesh.scale.set(20, 20, 20);
      mesh.rotation.set(
        degToRad(rotDeg[0]),
        degToRad(rotDeg[1]),
        degToRad(rotDeg[2]),
      );
      mesh.castShadow = true;
      mesh.receiveShadow = true;

      // Remove placeholder sphere
      const group = this.modelGroups[idx];
      if (!group) return;
      const old = group.getObjectByName(`jball_${idx}`);
      if (old) group.remove(old);
      group.add(mesh);
      // Link-0 STL replaces the placeholder as liftVisualNode
      if (idx === 0) {
        this.liftVisualNode = mesh;
        // Preserve current lift visual displacement on STL swap.
        // Otherwise a startup gap appears until the next lift event arrives.
        const motorUnits = this.liftInitialized ? this.liftCurrent : this.liftTarget;
        const disp = liftObliqueDisplacement_threeWu(motorUnits, this.liftCfg);
        mesh.position.set(disp.dx, disp.dy, disp.dz);
      }
      this.logTrace('stl:load:ok', { idx, generation });
    }, undefined, (err) => {
      console.warn(`[arm3d] link${idx} STL error:`, err);
      this.logTrace('stl:load:error', { idx, generation, error: String(err) });
    });
  }

  /**
   * Load base plate STL.
   * Positioned according to TRANSFORMS.basePlate config (meters → world units).
   */
  private loadBaseStl(generation: number): void {
    const bp = TRANSFORMS.basePlate;
    const S = WORLD.SCALE_FACTOR;
    const baseGroup = new THREE.Group();
    baseGroup.name = 'base-plate';
    baseGroup.position.set(
      bp.translation.x * S,
      bp.translation.z * S,  // ROS Z → Three Y
      -bp.translation.y * S, // ROS Y → Three -Z (standard mapping)
    );
    baseGroup.userData.basePos = {
      x: baseGroup.position.x,
      y: baseGroup.position.y,
      z: baseGroup.position.z,
    };
    this.baseGroupNode = baseGroup;
    (this.modelGroups[0] ?? this.rootGroup).add(baseGroup);

    this.stlLoader.load('/static/stl/base.stl', (geo: THREE.BufferGeometry) => {
      if (generation !== this.rebuildGeneration) {
        this.logTrace('stl:base:stale', { generation, currentGeneration: this.rebuildGeneration });
        return;
      }

      geo.computeVertexNormals();
      const mesh = new THREE.Mesh(geo, MAT_BASE());
      mesh.scale.set(20, 20, 20);
      // Same mesh rotation as legacy link meshes (meshRotationBaseDeg)
      mesh.rotation.set(degToRad(-90), 0, degToRad(-90), 'XYZ');
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      baseGroup.add(mesh);
      this.logTrace('stl:base:ok', { generation });
    }, undefined, (err) => {
      console.warn('[arm3d] base.stl error:', err);
      this.logTrace('stl:base:error', { generation, error: String(err) });
    });
  }

  /**
   * Load tool (vacuum gripper) STL onto the tool group.
   * Falls back to a cone placeholder on failure.
   */
  private loadToolStl(generation: number): void {
    if (!this.toolGroup) return;
    const tg = this.toolGroup;

    this.stlLoader.load(
      '/static/stl/xarm_vacuum_gripper.2f2a316.stl',
      (geo: THREE.BufferGeometry) => {
        if (generation !== this.rebuildGeneration) {
          this.logTrace('stl:tool:stale', { generation, currentGeneration: this.rebuildGeneration });
          return;
        }

        geo.computeVertexNormals();
        const mesh = new THREE.Mesh(geo, MAT_TOOL());
        // Legacy: SCALE=[.001,.001,.001] * WORLD_SCALE = 0.02
        // Mesh rotation: [180,0,0] + [-90,0,-90] = [90,0,-90] deg
        mesh.scale.set(0.02, 0.02, 0.02);
        mesh.rotation.set(degToRad(90), 0, degToRad(-90), 'XYZ');
        mesh.castShadow = true;
        mesh.name = 'tool-mesh';
        tg.add(mesh);
        this.logTrace('stl:tool:ok', { generation });
      },
      undefined,
      (err) => {
        if (generation !== this.rebuildGeneration) return;

        console.warn('[arm3d] tool STL error:', err);
        this.logTrace('stl:tool:error', { generation, error: String(err) });
        // Fallback cone
        const cone = new THREE.Mesh(
          new THREE.ConeGeometry(1.1, 4.5, 12),
          new THREE.MeshPhongMaterial({ color: 0xf59e0b }),
        );
        cone.rotation.x = Math.PI / 2;
        cone.name = 'tool-fallback-cone';
        tg.add(cone);
      },
    );
  }

  // ─── Camera mount (invisible, for reading world matrix) ────

  /**
   * Create an invisible camera mount group as a child of toolGroup.
   * Positioned identically to the visible frustum in debug-helpers.ts.
   * Used to read the camera world matrix for cloud positioning.
   */
  private createCameraMountGroup(): void {
    if (!this.toolGroup) return;
    // Remove old if rebuilding
    if (this.cameraMountGroup) {
      this.cameraMountGroup.parent?.remove(this.cameraMountGroup);
      this.cameraMountGroup = null;
    }
    const mount = new THREE.Group();
    mount.name = 'camera_mount_for_cloud';
    mount.visible = false; // invisible — only used for matrixWorld readout

    const ftc = TRANSFORMS.flangeToCamera;
    const pose = this.cameraMountPolicy.fromFlangeToCamera(ftc);
    this.cameraMountPolicy.applyToObject(mount, pose);
    mount.updateMatrix();

    this.toolGroup.add(mount);
    this.cameraMountGroup = mount;
  }

  /**
   * Force-update the camera mount world matrix and emit it on the bus.
   * Called after any scene graph change (FK, lift, mount, debug transform).
   */
  private emitCameraWorldMatrix(): void {
    if (!this.cameraMountGroup) return;
    this.cameraMountGroup.updateWorldMatrix(true, false);
    const elements = Array.from(this.cameraMountGroup.matrixWorld.elements);
    this.bus.emit('camera:worldMatrix', { elements });
  }

  // ─── Joint update (from orchestrator FK result) ───

  applyFKRotations(rotations: readonly import('@/domain/arm-kinematics').JointRotation[]): void {
    for (const rot of rotations) {
      const group = this.modelGroups[rot.groupIndex];
      if (!group) continue;

      if (rot.axis === 'x') {
        group.rotation.x = rot.angle;
      } else {
        group.rotation.y = rot.angle;
      }
    }
    this.emitCameraWorldMatrix();
  }

  // ─── Lift update ──────────────────────────────

  applyLift(motorUnits: number): void {
    if (this.modelGroups.length < 2) return;
    const g1 = this.modelGroups[1];
    if (!g1) return;

    const disp = liftObliqueDisplacement_threeWu(motorUnits, this.liftCfg);

    // Update lift visual node (groups[0] children position)
    if (this.liftVisualNode) {
      this.liftVisualNode.position.y = disp.dy;
      this.liftVisualNode.position.x = disp.dx;
      this.liftVisualNode.position.z = disp.dz;
    }

    // Restore base plate position relative to lift
    if (this.baseGroupNode?.userData?.basePos) {
      const bp = this.baseGroupNode.userData.basePos;
      this.baseGroupNode.position.set(bp.x, bp.y, bp.z);
    }

    // groups[1] position: base + lift displacement
    g1.position.y = group1AbsoluteY_threeWu(motorUnits, this.liftCfg);
    g1.position.x = disp.dx;
    g1.position.z = disp.dz;
    this.logTrace('lift:applied', {
      motorUnits,
      disp,
      g1: { x: g1.position.x, y: g1.position.y, z: g1.position.z },
    });
    this.emitCameraWorldMatrix();
  }

  // ─── Mount update ─────────────────────────────

  applyMount(mount: MountDegrees): void {
    this.lastMount = mount;
    this.applyRootDebugTransform();
    this.logTrace('mount:applied', {
      tiltDeg: mount.tilt,
      rotateDeg: mount.rotation,
    });
    this.lastAppliedMountKey = `${Number(mount.tilt).toFixed(3)}:${Number(mount.rotation).toFixed(3)}`;
    this.emitCameraWorldMatrix();
  }

  // ─── Debug controls ───────────────────────────

  setRootDebugRotation(rx: number, ry: number, rz: number): void {
    this.rootDebugRotation = [rx, ry, rz];
    this.applyRootDebugTransform();
  }

  setRootDebugPosition(px: number, py: number, pz: number): void {
    this.rootDebugPosition = [px, py, pz];
    this.applyRootDebugTransform();
  }

  private applyRootDebugTransform(): void {
    this.rootTransformPolicy.applyRootTransform(
      this.rootGroup,
      this.lastMount,
      this.rootDebugRotation as [number, number, number],
      this.rootDebugPosition as [number, number, number],
    );
  }

  setIgnoreMount(ignore: boolean): void {
    this.ignoreMount = ignore;
  }

  // ─── Getters ──────────────────────────────────

  getToolGroup(): THREE.Group | null {
    return this.toolGroup;
  }

  getCameraMountGroup(): THREE.Group | null {
    return this.cameraMountGroup;
  }

  getVariant(): ArmVariant {
    return this.variant;
  }

  // ─── Per-frame update (called from render loop) ───

  /**
   * Called once per frame. Smoothly interpolates lift position
   * toward target using exponential ease-out.
   */
  update(): void {
    if (!this.liftInitialized) return;

    const delta = this.liftTarget - this.liftCurrent;
    if (Math.abs(delta) <= ArmVisual.LIFT_SNAP_THRESHOLD) return;

    this.liftCurrent += delta * ArmVisual.LIFT_LERP_FACTOR;
    if (Math.abs(this.liftTarget - this.liftCurrent) < ArmVisual.LIFT_SNAP_THRESHOLD) {
      this.liftCurrent = this.liftTarget;
    }
    this.applyLift(this.liftCurrent);
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
