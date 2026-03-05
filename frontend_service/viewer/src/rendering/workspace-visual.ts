/**
 * workspace-visual.ts — Robot workspace boundary visualization.
 *
 * Renders the xArm's configured workspace (ROBOT_ARM.workspace) as a
 * translucent wireframe box in the 3D scene, anchored to the arm base.
 *
 * The workspace is defined in ROS convention (Z-up, mm):
 *   dimensions: [X, Y, Z] mm
 *   basePositionInWS: [X, Y, Z] mm — arm base origin within the workspace
 *
 * Activated via `?workspace` query param.
 */

import * as THREE from 'three';
import { ROBOT_ARM, WORLD, WORKSPACE_VIS } from '@/config/scene-config';

// ─── Coordinate helpers (ROS mm → Three.js world units) ────

/**
 * Convert a ROS-frame point (mm) to Three.js Y-up world units.
 * ROS Z-up → Three.js Y-up: Three.x = ROS.x, Three.y = ROS.z, Three.z = -ROS.y
 * Then scale: mm → m → world units (÷1000 × SCALE_FACTOR)
 */
function rosMmToThreeWu(
  rosX_mm: number, rosY_mm: number, rosZ_mm: number,
  scale: number,
): THREE.Vector3 {
  return new THREE.Vector3(
    (rosX_mm / 1000) * scale,    // Three.x = ROS.x
    (rosZ_mm / 1000) * scale,    // Three.y = ROS.z
    (-rosY_mm / 1000) * scale,   // Three.z = -ROS.y
  );
}

// ─── Box geometry from workspace config ────────────────────

export interface WorkspaceBoxBounds {
  /** Min corner in Three.js world units, relative to arm base. */
  min: THREE.Vector3;
  /** Max corner in Three.js world units, relative to arm base. */
  max: THREE.Vector3;
  /** Center in Three.js world units, relative to arm base. */
  center: THREE.Vector3;
  /** Size in Three.js world units. */
  size: THREE.Vector3;
}

/**
 * Compute the workspace bounding box in Three.js world units,
 * relative to the arm base (groups[0] origin).
 */
export function computeWorkspaceBounds(
  dimensions_mm: readonly [number, number, number],
  baseInWS_mm: readonly [number, number, number],
  scaleFactor: number,
): WorkspaceBoxBounds {
  // Workspace spans from 0→dim in each ROS axis.
  // Arm base is at baseInWS within that box.
  // So base-relative: min = -baseInWS, max = dim - baseInWS.
  const [dimX, dimY, dimZ] = dimensions_mm;
  const [bx, by, bz] = baseInWS_mm;

  const rosMin = { x: -bx, y: -by, z: -bz };
  const rosMax = { x: dimX - bx, y: dimY - by, z: dimZ - bz };

  const min = rosMmToThreeWu(rosMin.x, rosMin.y, rosMin.z, scaleFactor);
  const max = rosMmToThreeWu(rosMax.x, rosMax.y, rosMax.z, scaleFactor);

  // After ROS→Three conversion, min/max components may be swapped
  // (because Three.z = -ROS.y flips the axis). Normalize:
  const actualMin = new THREE.Vector3(
    Math.min(min.x, max.x),
    Math.min(min.y, max.y),
    Math.min(min.z, max.z),
  );
  const actualMax = new THREE.Vector3(
    Math.max(min.x, max.x),
    Math.max(min.y, max.y),
    Math.max(min.z, max.z),
  );

  const center = new THREE.Vector3().addVectors(actualMin, actualMax).multiplyScalar(0.5);
  const size = new THREE.Vector3().subVectors(actualMax, actualMin);

  return { min: actualMin, max: actualMax, center, size };
}

// ─── Dimension label (canvas sprite) ───────────────────────

function makeDimensionLabel(
  text: string,
  position: THREE.Vector3,
  color: string,
): THREE.Sprite {
  const canvas = document.createElement('canvas');
  canvas.width = 128;
  canvas.height = 48;
  const ctx = canvas.getContext('2d')!;
  ctx.clearRect(0, 0, 128, 48);
  ctx.fillStyle = color;
  ctx.font = 'bold 20px monospace';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(text, 64, 24);

  const tex = new THREE.CanvasTexture(canvas);
  const mat = new THREE.SpriteMaterial({
    map: tex,
    depthTest: false,
    transparent: true,
  });
  const sprite = new THREE.Sprite(mat);
  sprite.position.copy(position);
  sprite.scale.set(4, 1.5, 1);
  return sprite;
}

// ─── WorkspaceVisual component ─────────────────────────────

export interface WorkspaceVisualOptions {
  /** The arm's rootGroup to attach workspace visualization to. */
  armRootGroup: THREE.Group;
  /** Offset of groups[0] (arm base carrier) within rootGroup (Three.js Y-up wu). */
  baseGroupOffset?: THREE.Vector3;
}

export class WorkspaceVisual {
  private readonly group: THREE.Group;
  private disposed = false;

  constructor(opts: WorkspaceVisualOptions) {
    this.group = new THREE.Group();
    this.group.name = 'workspace-bounds';

    const bounds = computeWorkspaceBounds(
      ROBOT_ARM.workspace.dimensions,
      ROBOT_ARM.workspace.basePositionInWS,
      WORLD.SCALE_FACTOR,
    );

    // ── Wireframe edges ──────────────────────────
    const boxGeo = new THREE.BoxGeometry(bounds.size.x, bounds.size.y, bounds.size.z);
    const edges = new THREE.EdgesGeometry(boxGeo);
    const edgeMat = new THREE.LineBasicMaterial({
      color: WORKSPACE_VIS.edgeColor,
      linewidth: WORKSPACE_VIS.lineWidth,
      transparent: true,
      opacity: 0.7,
    });
    const wireframe = new THREE.LineSegments(edges, edgeMat);
    wireframe.position.copy(bounds.center);
    this.group.add(wireframe);

    // ── Translucent fill ─────────────────────────
    if (WORKSPACE_VIS.fillOpacity > 0) {
      const fillMat = new THREE.MeshBasicMaterial({
        color: WORKSPACE_VIS.fillColor,
        transparent: true,
        opacity: WORKSPACE_VIS.fillOpacity,
        side: THREE.DoubleSide,
        depthWrite: false,
      });
      const fillMesh = new THREE.Mesh(boxGeo.clone(), fillMat);
      fillMesh.position.copy(bounds.center);
      this.group.add(fillMesh);
    }

    // ── Dimension labels ─────────────────────────
    if (WORKSPACE_VIS.showLabels) {
      const [dimX, dimY, dimZ] = ROBOT_ARM.workspace.dimensions;
      const labelColor = WORKSPACE_VIS.labelColor;

      // X dimension label: along the bottom-front edge
      const xLabel = makeDimensionLabel(
        `${dimX}mm`,
        new THREE.Vector3(bounds.center.x, bounds.min.y - 1.0, bounds.max.z + 0.5),
        labelColor,
      );
      this.group.add(xLabel);

      // Y dimension label (ROS Y → Three.js -Z): along the side edge
      const yLabel = makeDimensionLabel(
        `${dimY}mm`,
        new THREE.Vector3(bounds.max.x + 1.5, bounds.min.y - 1.0, bounds.center.z),
        labelColor,
      );
      this.group.add(yLabel);

      // Z dimension label (ROS Z → Three.js +Y): along the vertical edge
      const zLabel = makeDimensionLabel(
        `${dimZ}mm`,
        new THREE.Vector3(bounds.max.x + 1.5, bounds.center.y, bounds.max.z + 0.5),
        labelColor,
      );
      this.group.add(zLabel);
    }

    // ── Position within rootGroup ────────────────
    // The workspace is relative to the arm base (groups[0]).
    // groups[0] is at a fixed offset within rootGroup.
    if (opts.baseGroupOffset) {
      this.group.position.copy(opts.baseGroupOffset);
    }

    opts.armRootGroup.add(this.group);
  }

  /** Set visibility programmatically. */
  setVisible(visible: boolean): void {
    this.group.visible = visible;
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.group.parent?.remove(this.group);
    this.group.traverse((obj) => {
      if (obj instanceof THREE.Mesh) {
        obj.geometry.dispose();
        if (Array.isArray(obj.material)) obj.material.forEach((m) => m.dispose());
        else obj.material.dispose();
      }
      if (obj instanceof THREE.LineSegments) {
        obj.geometry.dispose();
        (obj.material as THREE.Material).dispose();
      }
      if (obj instanceof THREE.Sprite) {
        (obj.material as THREE.SpriteMaterial).map?.dispose();
        obj.material.dispose();
      }
    });
  }
}
