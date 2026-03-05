/**
 * agv-map-visual.ts — AGV map layer + robot pose visualization.
 *
 * Rendering layer. Loads the Symovo occupancy grid map image as a textured
 * plane in the 3D scene, and moves the arm root group based on AGV pose.
 *
 * Activated via `?agv_map` or `AGV_VISUALIZATION.enabled = true`.
 */

import * as THREE from 'three';
import type { EventBus } from '@/event-bus';
import type { AgvPose, AgvMapMeta } from '@/types/transforms';
import {
  AGV_VISUALIZATION,
  TRANSFORMS,
  SCENE_CAMERA,
} from '@/config/scene-config';
import { metersToWorld, rosMetersToThreeWorld } from '@/domain/coord-utils';
import { isTraceEnabled, trace } from '@/utils/trace';
import { AgvPosePolicy } from '@/rendering/agv-pose-policy';

const ML = AGV_VISUALIZATION.mapLayer;
const POSE_CFG = AGV_VISUALIZATION.pose;

export interface AgvMapVisualOptions {
  scene: THREE.Scene;
  bus: EventBus;
  /** The arm root group that will be moved by AGV pose. */
  armRootGroup: THREE.Group;
  /** Camera + controls for far plane extension. */
  camera: THREE.PerspectiveCamera;
  controls: import('three/addons/controls/OrbitControls.js').OrbitControls;
}

export class AgvMapVisual {
  private readonly scene: THREE.Scene;
  private readonly bus: EventBus;
  private readonly armRootGroup: THREE.Group;
  private readonly camera: THREE.PerspectiveCamera;
  private readonly controls: import('three/addons/controls/OrbitControls.js').OrbitControls;

  private mapPivot: THREE.Group | null = null;
  private mapMeta: AgvMapMeta | null = null;
  private disposed = false;
  private cameraFocused = false;
  private lastMapMismatchKey = '';
  private readonly traceOn: boolean;
  private readonly posePolicy: AgvPosePolicy;
  private agvBaseCaptured = false;
  private agvBasePosition = new THREE.Vector3();

  constructor(opts: AgvMapVisualOptions) {
    this.scene = opts.scene;
    this.bus = opts.bus;
    this.armRootGroup = opts.armRootGroup;
    this.camera = opts.camera;
    this.controls = opts.controls;
    this.traceOn = isTraceEnabled();
    this.posePolicy = new AgvPosePolicy(POSE_CFG, TRANSFORMS.agvToArmBase);

    // Extend camera far plane and controls to accommodate large map
    this.camera.far = 5000;
    this.camera.updateProjectionMatrix();
    this.controls.maxDistance = 2000;

    // Re-capture baseline after arm rebuild/re-init.
    this.bus.on('arm:init', () => {
      this.agvBaseCaptured = false;
    });

    // Wire AGV pose events
    this.bus.on('agv:pose', (pose) => this.applyPose(pose));

    // Load map on construction
    this.loadMap();
  }

  // ─── Map loading ─────────────────────────────

  private async loadMap(): Promise<void> {
    try {
      const meta = await this.fetchMapMetadata();
      if (this.disposed) return;
      if (!meta) {
        // No map available — this is normal when symovo is not configured
        return;
      }
      this.mapMeta = meta;
      this.loadTexture(meta);
    } catch (err) {
      console.warn('[agv-map] Map load error:', err);
    }
  }

  private async fetchMapMetadata(): Promise<AgvMapMeta | null> {
    // Try list endpoint first
    let resp: Response;
    try {
      resp = await fetch(ML.mapListUrl);
    } catch {
      // Network error — symovo service unreachable
      return null;
    }
    if (!resp.ok) {
      // 404/5xx — map service not available or no maps; this is normal
      if (resp.status !== 404) {
        console.warn(`[agv-map] Map list returned ${resp.status}`);
      }
      return null;
    }

    const data = await resp.json();

    // API wraps list in { result: [...] } — unwrap
    let mapEntry: any = null;
    const resultArr = Array.isArray(data?.result) ? data.result : null;
    if (resultArr) {
      mapEntry = resultArr.find((m: any) => m.id === ML.preferredMapId) ?? resultArr[0];
    } else if (Array.isArray(data)) {
      mapEntry = data.find((m: any) => m.id === ML.preferredMapId) ?? data[0];
    } else if (data?.id) {
      mapEntry = data;
    }

    if (!mapEntry) {
      // Fallback: try specific map endpoint
      const metaUrl = ML.mapMetaUrlTemplate.replace('{map_id}', String(ML.preferredMapId));
      try {
        const fallbackResp = await fetch(metaUrl);
        if (fallbackResp.ok) mapEntry = await fallbackResp.json();
      } catch {
        // Silently ignore fetch errors
      }
    }

    if (!mapEntry) return null;

    // size can be [width, height] array or separate width/height fields
    const sizeArr = Array.isArray(mapEntry.size) ? mapEntry.size : null;
    const widthPx = sizeArr ? Number(sizeArr[0]) : (Number(mapEntry.width) || 0);
    const heightPx = sizeArr ? Number(sizeArr[1]) : (Number(mapEntry.height) || 0);
    const imageUrl = ML.mapImageUrlTemplate.replace('{map_id}', String(mapEntry.id));

    return {
      id: mapEntry.id,
      widthPx,
      heightPx,
      resolution: mapEntry.resolution ?? 0.05,
      offsetX: mapEntry.offsetX ?? mapEntry.offset_x ?? 0,
      offsetY: mapEntry.offsetY ?? mapEntry.offset_y ?? 0,
      imageUrl,
    };
  }

  private loadTexture(meta: AgvMapMeta): void {
    const widthM = meta.widthPx * meta.resolution;
    const heightM = meta.heightPx * meta.resolution;

    const loader = new THREE.TextureLoader();
    loader.load(
      meta.imageUrl,
      (texture) => {
        if (this.disposed) {
          texture.dispose();
          return;
        }
        texture.colorSpace = THREE.SRGBColorSpace;
        this.buildMapPlane(texture, widthM, heightM, meta.offsetX, meta.offsetY);
      },
      undefined,
      (err) => console.warn('[agv-map] Texture load error:', err),
    );
  }

  /**
   * Build the map plane and place it in the scene.
   *
   * Occupancy grid:
   *   - offsetX/offsetY = ROS position (m) of the bottom-left pixel
   *   - size * resolution = dimensions in meters
   *
   * The plane is laid flat on the XZ ground plane (rotation.x = -π/2).
   * No extra axis rotation — the debug RY slider handles any needed yaw.
   * After rot.x = -π/2:
   *   - image u (left→right) → Three +X
   *   - image v (bottom→top) → Three -Z
   */
  private buildMapPlane(
    texture: THREE.Texture,
    widthM: number,
    heightM: number,
    offsetX: number,
    offsetY: number,
  ): void {
    const widthWu = metersToWorld(widthM);
    const heightWu = metersToWorld(heightM);

    texture.wrapS = THREE.ClampToEdgeWrapping;
    texture.wrapT = THREE.ClampToEdgeWrapping;

    // Plane mesh
    const geo = new THREE.PlaneGeometry(widthWu, heightWu);
    const mat = new THREE.MeshBasicMaterial({
      map: texture,
      transparent: true,
      opacity: ML.opacity,
      side: THREE.DoubleSide,
      depthWrite: false,
    });
    const plane = new THREE.Mesh(geo, mat);
    plane.name = 'agv_map_layer';
    plane.renderOrder = -2;

    // Lay flat on XZ ground plane
    plane.rotation.x = -Math.PI / 2;

    // Offset so bottom-left UV(0,0) sits at pivot origin.
    // After rot.x=-π/2, BL vertex (-w/2,-h/2,0) → (-w/2, 0, h/2).
    // Shift by (+w/2, 0, -h/2) → BL lands at (0, 0, 0).
    plane.position.set(widthWu / 2, 0, -heightWu / 2);

    // Pivot — positioned at API offsets; debug sliders add on top
    const pivot = new THREE.Group();
    pivot.name = 'agv_map_pivot';
    pivot.add(plane);

    // Place pivot at occupancy grid origin.
    // API offsets are in the same coord frame as AGV pose, which uses
    // swapXY + invertY before rosToThree. Apply the same remapping here.
    const origin = rosMetersToThreeWorld({ x: offsetY, y: -offsetX, z: 0 });
    pivot.position.set(origin.x, ML.yOffset_wu, origin.z);
    this.mapBasePosition.set(origin.x, ML.yOffset_wu, origin.z);

    if (this.traceOn) {
      trace('agv-map', 'map placed', {
        offsetX, offsetY, widthM, heightM,
        widthWu, heightWu,
        pivotPos: { x: origin.x, y: ML.yOffset_wu, z: origin.z },
      });
    }

    this.mapPivot = pivot;
    this.scene.add(pivot);
  }

  private mapBasePosition = new THREE.Vector3();

  /**
   * Apply debug transform from map sliders.
   * pos = [x, y, z] in world units (additive to API offset), rot = [rx, ry, rz] in degrees.
   */
  setDebugTransform(
    pos: [number, number, number],
    rot: [number, number, number],
  ): void {
    if (!this.mapPivot) return;
    this.mapPivot.position.set(
      this.mapBasePosition.x + pos[0],
      this.mapBasePosition.y + pos[1],
      this.mapBasePosition.z + pos[2],
    );
    this.mapPivot.rotation.set(
      (rot[0] * Math.PI) / 180,
      (rot[1] * Math.PI) / 180,
      (rot[2] * Math.PI) / 180,
    );
  }

  // ─── AGV pose ────────────────────────────────

  private applyPose(pose: AgvPose): void {
    // map_id <= 0 means unknown/unset map from upstream; skip mismatch checks in this case.
    if (!Number.isFinite(pose.map_id) || pose.map_id <= 0) {
      return;
    }

    // Check map_id matches
    if (this.mapMeta && pose.map_id !== this.mapMeta.id) {
      const mismatchKey = `${pose.map_id}->${this.mapMeta.id}`;
      if (mismatchKey !== this.lastMapMismatchKey) {
        this.lastMapMismatchKey = mismatchKey;
        console.warn(`[agv-map] Pose map_id=${pose.map_id} != loaded map ${this.mapMeta.id}`);
        if (this.traceOn) {
          trace('agv-map', 'pose map mismatch', {
            poseMapId: pose.map_id,
            loadedMapId: this.mapMeta.id,
            pose,
          });
        }
      }
      return;
    }

    const resolvedPose = this.posePolicy.resolve(pose);

    // Convert to Three.js world coordinates
    const tw = rosMetersToThreeWorld({
      x: resolvedPose.arm.xM,
      y: resolvedPose.arm.yM,
      z: 0,
    });

    // Capture arm root baseline once and apply AGV transform additively.
    // This preserves canonical/model calibration rotation and avoids random-looking tilt.
    if (!this.agvBaseCaptured) {
      this.agvBaseCaptured = true;
      this.agvBasePosition.copy(this.armRootGroup.position);
      if (this.traceOn) {
        trace('agv-map', 'agv base captured', {
          basePosition: {
            x: this.agvBasePosition.x,
            y: this.agvBasePosition.y,
            z: this.agvBasePosition.z,
          },
        });
      }
    }

    this.armRootGroup.position.set(
      this.agvBasePosition.x + tw.x,
      this.agvBasePosition.y + tw.y,
      this.agvBasePosition.z + tw.z,
    );

    // IMPORTANT: do not rotate armRoot here.
    // Root orientation authority belongs to arm baseline + mount pipeline.
    // AGV layer in renderer contributes only world position offset.

    // Focus camera on robot once
    if (!this.cameraFocused) {
      this.cameraFocused = true;
      const cpY = SCENE_CAMERA.initialPosition[1];
      this.controls.target.set(tw.x, cpY * 0.5, tw.z);
      this.camera.position.set(tw.x + 20, cpY, tw.z + 20);
      this.controls.update();
    }
  }

  // ─── Dispose ─────────────────────────────────

  dispose(): void {
    this.disposed = true;
    if (this.mapPivot) {
      this.mapPivot.traverse((obj) => {
        if (obj instanceof THREE.Mesh) {
          obj.geometry?.dispose();
          const mat = obj.material as THREE.MeshBasicMaterial;
          mat.map?.dispose();
          mat.dispose();
        }
      });
      this.scene.remove(this.mapPivot);
      this.mapPivot = null;
    }
  }
}
