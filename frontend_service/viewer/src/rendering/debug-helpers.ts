/**
 * debug-helpers.ts — Debug visualization helpers for the 3D scene.
 *
 * Rendering layer. Provides:
 *   - ROS-convention axis arrows (labeled, colored: X=red, Y=green, Z=blue)
 *   - D435 camera frustum wireframe
 *   - Camera mount group (attaches to tool flange)
 *   - Camera-local axes
 *
 * All helpers are toggled via URL query params processed in main.ts.
 * Standard ROS→Three mapping: ROS X→-Z, ROS Y→-X, ROS Z→+Y.
 */

import * as THREE from 'three';
import {
  DEPTH_CAMERA,
  TRANSFORMS,
  WORLD,
} from '@/config/scene-config';
import { metersToWorld } from '@/domain/coord-utils';
import type { EventBus } from '@/event-bus';
import { CameraMountPolicy, type CameraDebugTransform } from '@/rendering/camera-mount-policy';

const cameraMountPolicy = new CameraMountPolicy(WORLD.SCALE_FACTOR);

// ─── Axis arrow primitive ──────────────────────────

/**
 * Create a labeled axis arrow (ArrowHelper + text sprite).
 */
function makeAxisArrow(
  dir: THREE.Vector3,
  origin: THREE.Vector3,
  length: number,
  color: number,
  label: string,
): THREE.Group {
  const group = new THREE.Group();

  // Arrow
  const arrow = new THREE.ArrowHelper(
    dir.clone().normalize(),
    origin.clone(),
    length,
    color,
    length * 0.12,
    length * 0.06,
  );
  group.add(arrow);

  // Text label (canvas sprite)
  const canvas = document.createElement('canvas');
  canvas.width = 64;
  canvas.height = 32;
  const ctx = canvas.getContext('2d')!;
  ctx.fillStyle = '#' + color.toString(16).padStart(6, '0');
  ctx.font = 'bold 24px monospace';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(label, 32, 16);

  const tex = new THREE.CanvasTexture(canvas);
  const mat = new THREE.SpriteMaterial({ map: tex, depthTest: false });
  const sprite = new THREE.Sprite(mat);
  sprite.scale.set(2, 1, 1);

  const labelPos = origin
    .clone()
    .add(dir.clone().normalize().multiplyScalar(length * 1.15));
  sprite.position.copy(labelPos);
  group.add(sprite);

  return group;
}

// ─── ROS Axes ──────────────────────────────────────

/**
 * Create ROS-convention axes at a given origin in Three.js space.
 * Standard mapping:
 *   ROS X (red)   → Three.js -Z
 *   ROS Y (green) → Three.js -X
 *   ROS Z (blue)  → Three.js +Y
 *
 * @param origin — position in Three.js world units
 * @param lengthM — axis length in meters (default 1m = 20wu)
 * @param prefix — label prefix (e.g. "TCP " → "TCP X")
 */
export function createRosAxes(
  origin?: THREE.Vector3,
  lengthM = 1,
  prefix = '',
): THREE.Group {
  const o = origin ?? new THREE.Vector3(0, 0, 0);
  const len = metersToWorld(lengthM);

  const group = new THREE.Group();
  group.name = `ros_axes_${prefix || 'world'}`;

  // ROS X (forward) → Three -Z
  group.add(makeAxisArrow(new THREE.Vector3(0, 0, -1), o, len, 0xff3333, prefix + 'X'));
  // ROS Y (left) → Three -X
  group.add(makeAxisArrow(new THREE.Vector3(-1, 0, 0), o, len, 0x33ff33, prefix + 'Y'));
  // ROS Z (up) → Three +Y
  group.add(makeAxisArrow(new THREE.Vector3(0, 1, 0), o, len, 0x3388ff, prefix + 'Z'));

  return group;
}

// ─── Camera frustum ────────────────────────────────

/**
 * Build frustum wireframe geometry extending along local -Z.
 * Uses DEPTH_CAMERA intrinsics for FOV computation.
 */
function createFrustumGeometry(): THREE.LineSegments {
  const cam = DEPTH_CAMERA;
  const nearWU = metersToWorld(cam.frustum.near);
  const farWU = metersToWorld(cam.frustum.far);

  // FOV from intrinsics
  const halfH = Math.tan((cam.fov.h * Math.PI) / 360); // half horizontal angle
  const halfV = Math.tan((cam.fov.v * Math.PI) / 360); // half vertical angle

  const nw = nearWU * halfH;
  const nh = nearWU * halfV;
  const fw = farWU * halfH;
  const fh = farWU * halfV;

  // Frustum along local -Z (camera optical axis)
  const verts = new Float32Array([
    // near TL, TR, BR, BL
    -nw, nh, -nearWU,
    nw, nh, -nearWU,
    nw, -nh, -nearWU,
    -nw, -nh, -nearWU,
    // far TL, TR, BR, BL
    -fw, fh, -farWU,
    fw, fh, -farWU,
    fw, -fh, -farWU,
    -fw, -fh, -farWU,
  ]);

  const idx = new Uint16Array([
    0, 1, 1, 2, 2, 3, 3, 0, // near rect
    4, 5, 5, 6, 6, 7, 7, 4, // far rect
    0, 4, 1, 5, 2, 6, 3, 7, // connecting edges
  ]);

  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(verts, 3));
  geo.setIndex(new THREE.BufferAttribute(idx, 1));

  const mat = new THREE.LineBasicMaterial({
    color: 0x00ccaa,
    transparent: true,
    opacity: 0.6,
    depthTest: true,
  });

  const frustum = new THREE.LineSegments(geo, mat);
  frustum.name = 'd435_frustum';
  return frustum;
}

/**
 * Create camera-local axes (small, mounted on camera group):
 *   cam X (right in image)   → mount local +X → red
 *   cam Y (down in image)    → mount local +Y → green
 *   cam Z (depth/optical)    → mount local -Z → blue
 */
function createCameraLocalAxes(lengthM = 0.05): THREE.Group {
  const len = metersToWorld(lengthM);
  const o = new THREE.Vector3(0, 0, 0);
  const group = new THREE.Group();
  group.name = 'cam_local_axes';

  group.add(makeAxisArrow(new THREE.Vector3(1, 0, 0), o, len, 0xff4444, 'cX'));
  group.add(makeAxisArrow(new THREE.Vector3(0, 1, 0), o, len, 0x44ff44, 'cY'));
  group.add(makeAxisArrow(new THREE.Vector3(0, 0, -1), o, len, 0x4488ff, 'cZ'));

  return group;
}

/**
 * Create a D435 camera mount group with:
 *   - mount point marker (small magenta sphere)
 *   - frustum wireframe
 *   - camera-local axes
 *
 * Positioned/rotated via TRANSFORMS.flangeToCamera.
 * Attach the returned group to the tool (flange) group.
 */
export function createCameraMount(includeFrustum = true): THREE.Group {
  const mount = new THREE.Group();
  mount.name = 'camera_mount_d435';

  // Position & rotation from flangeToCamera transform (meters → Three world)
  const ftc = TRANSFORMS.flangeToCamera;
  const canonicalMountPose = cameraMountPolicy.fromFlangeToCamera(ftc);
  cameraMountPolicy.applyToObject(mount, canonicalMountPose);
  mount.updateMatrix();
  mount.updateWorldMatrix(true, false);

  // Mount point marker
  const markerGeo = new THREE.SphereGeometry(metersToWorld(0.008), 12, 8);
  const markerMat = new THREE.MeshBasicMaterial({
    color: 0xff00ff,
    transparent: true,
    opacity: 0.85,
  });
  const marker = new THREE.Mesh(markerGeo, markerMat);
  marker.name = 'cam_mount_marker';
  mount.add(marker);

  // Frustum wireframe
  if (includeFrustum) {
    mount.add(createFrustumGeometry());
  }

  // Camera-local axes
  mount.add(createCameraLocalAxes(0.05));

  return mount;
}

// ─── DebugHelpers class ────────────────────────────

/** Temporary world-origin marker: red ring + pole + label at (0,0,0). */
function createWorldOriginMarker(): THREE.Group {
  const group = new THREE.Group();
  group.name = 'world-origin-marker';

  // Red ring on ground (XZ plane in Three.js Y-up)
  const ringGeo = new THREE.RingGeometry(8, 10, 32);
  const ringMat = new THREE.MeshBasicMaterial({ color: 0xff0000, side: THREE.DoubleSide });
  const ring = new THREE.Mesh(ringGeo, ringMat);
  ring.rotation.x = -Math.PI / 2;
  group.add(ring);

  // Vertical pole
  const poleGeo = new THREE.CylinderGeometry(1, 1, 40, 8);
  const poleMat = new THREE.MeshBasicMaterial({ color: 0xff0000 });
  const pole = new THREE.Mesh(poleGeo, poleMat);
  pole.position.y = 20;
  group.add(pole);

  // Label sprite
  const canvas = document.createElement('canvas');
  canvas.width = 256;
  canvas.height = 128;
  const ctx = canvas.getContext('2d')!;
  ctx.fillStyle = '#ff0000';
  ctx.font = 'bold 48px monospace';
  ctx.fillText('(0,0)', 20, 80);
  const tex = new THREE.CanvasTexture(canvas);
  const label = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex }));
  label.position.set(5, 45, 0);
  label.scale.set(30, 15, 1);
  group.add(label);

  group.position.set(0, 0, 0);
  return group;
}

export interface DebugHelpersOptions {
  scene: THREE.Scene;
  bus: EventBus;
  showAxes?: boolean | 'all';
  showFrustum?: boolean;
  getToolGroup?: () => THREE.Group | null;
}

/**
 * Manages debug visualizations in the scene.
 * Activated by URL query params: ?axes, ?axes=all, ?camera_frustum
 */
export class DebugHelpers {
  private readonly scene: THREE.Scene;
  private readonly bus: EventBus;
  private readonly createdObjects: THREE.Object3D[] = [];
  private cameraMountRef: THREE.Group | null = null;
  private cameraDebugTransform: CameraDebugTransform | null = null;

  constructor(opts: DebugHelpersOptions) {
    this.scene = opts.scene;
    this.bus = opts.bus;

    this.bus.on('debug:cameraTransform', (tf) => {
      this.cameraDebugTransform = tf;
      if (this.cameraMountRef) {
        this.applyCameraDebugTransform(this.cameraMountRef, tf);
      }
    });

    if (opts.showAxes) {
      // World origin axes
      const worldAxes = createRosAxes(undefined, 1, '');
      this.scene.add(worldAxes);
      this.createdObjects.push(worldAxes);

      // Temporary world origin marker (0,0)
      const originMarker = createWorldOriginMarker();
      this.scene.add(originMarker);
      this.createdObjects.push(originMarker);

      if (opts.showAxes === 'all') {
        // TCP and cam axes are added when tool group becomes available
        this.bus.on('fk:updated', () => {
          if (this.cameraMountRef) return; // already created
          const toolGroup = opts.getToolGroup?.();
          if (!toolGroup) return;

          // TCP axes on tool flange
          const tcpAxes = createRosAxes(undefined, 0.15, 'TCP ');
          toolGroup.add(tcpAxes);
          this.createdObjects.push(tcpAxes);

          // Camera mount with frustum on tool
          const camMount = createCameraMount(opts.showFrustum !== false);
          if (this.cameraDebugTransform) {
            this.applyCameraDebugTransform(camMount, this.cameraDebugTransform);
          }
          toolGroup.add(camMount);
          this.cameraMountRef = camMount;
          this.createdObjects.push(camMount);
        });
      }
    } else if (opts.showFrustum) {
      // Just frustum, no world axes
      this.bus.on('fk:updated', () => {
        if (this.cameraMountRef) return;
        const toolGroup = opts.getToolGroup?.();
        if (!toolGroup) return;

        const camMount = createCameraMount(true);
        if (this.cameraDebugTransform) {
          this.applyCameraDebugTransform(camMount, this.cameraDebugTransform);
        }
        toolGroup.add(camMount);
        this.cameraMountRef = camMount;
        this.createdObjects.push(camMount);
      });
    }
  }

  private applyCameraDebugTransform(
    mount: THREE.Group,
    tf: CameraDebugTransform,
  ): void {
    const debugPose = cameraMountPolicy.fromDebugTransform(tf);
    cameraMountPolicy.applyToObject(mount, debugPose);
    mount.updateMatrix();
    mount.updateWorldMatrix(true, false);
  }

  getCameraMount(): THREE.Group | null {
    return this.cameraMountRef;
  }

  dispose(): void {
    for (const obj of this.createdObjects) {
      obj.parent?.remove(obj);
      obj.traverse((child) => {
        if (child instanceof THREE.Mesh) {
          child.geometry?.dispose();
          const mat = child.material;
          if (Array.isArray(mat)) mat.forEach((m) => m.dispose());
          else mat?.dispose();
        }
        if (child instanceof THREE.LineSegments) {
          child.geometry?.dispose();
          (child.material as THREE.Material)?.dispose();
        }
      });
    }
    this.createdObjects.length = 0;
    this.cameraMountRef = null;
  }
}
