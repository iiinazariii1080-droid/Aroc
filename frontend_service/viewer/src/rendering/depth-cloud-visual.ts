/**
 * depth-cloud-visual.ts — Three.js point cloud renderer for depth frames.
 *
 * Rendering layer. Takes domain PointCloud (camera-frame, meters) and renders
 * it in the Three.js scene using the transform chain.
 *
 * Key fix from audit: sizeAttenuation = false, point size in screen pixels.
 * This eliminates the pixel shift bug when changing camera distance.
 */

import * as THREE from 'three';
import type { EventBus } from '@/event-bus';
import type { PointCloud } from '@/types/depth';
import { DEPTH_OVERLAY_RENDER, WORLD } from '@/config/scene-config';
import { domainPoseToThreeMatrix } from './coord-converter';
import type { Mat4 } from '@/types/coordinates';
import { mat4Identity } from '@/domain/coord-utils';

export class DepthCloudVisual {
  private readonly scene: THREE.Scene;
  private readonly bus: EventBus;

  /** Points object for the live depth cloud. */
  private points: THREE.Points | null = null;

  /** Points object for the accumulated voxel map. */
  private mapPoints: THREE.Points | null = null;

  /** Current world→camera transform for positioning the cloud. */
  private worldToCamera: Mat4 = mat4Identity();

  /** Whether depth cloud is visible. */
  private visible = false;

  constructor(scene: THREE.Scene, bus: EventBus) {
    this.scene = scene;
    this.bus = bus;

    // Wire events
    this.bus.on('depth:cloud', (cloud) => {
      if (this.visible) this.updateCloud(cloud);
    });

    this.bus.on('transform:resolved', (poses) => {
      this.worldToCamera = poses.worldToCamera;
    });

    this.bus.on('ui:toggleDepth', (on) => {
      this.visible = on;
      if (this.points) this.points.visible = on;
    });

    this.bus.on('voxel:updated', (voxels) => {
      this.updateVoxelMap(voxels);
    });

    this.bus.on('voxel:clear', () => {
      this.clearVoxelMap();
    });
  }

  // ─── Live cloud ───────────────────────────────

  private updateCloud(cloud: PointCloud): void {
    if (this.points) {
      this.scene.remove(this.points);
      this.points.geometry.dispose();
      (this.points.material as THREE.Material).dispose();
      this.points = null;
    }

    if (cloud.count === 0) return;

    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(cloud.positions, 3));
    geometry.setAttribute('color', new THREE.Float32BufferAttribute(cloud.colors, 3));

    const material = new THREE.PointsMaterial({
      size: DEPTH_OVERLAY_RENDER.pointSizePx,
      sizeAttenuation: false,      // ← FIX: screen-space points, no distance scaling
      vertexColors: true,
      depthTest: DEPTH_OVERLAY_RENDER.depthTest,
      depthWrite: DEPTH_OVERLAY_RENDER.depthWrite,
      opacity: DEPTH_OVERLAY_RENDER.opacity,
      transparent: DEPTH_OVERLAY_RENDER.opacity < 1,
    });

    this.points = new THREE.Points(geometry, material);
    this.points.name = 'depth-cloud-live';
    this.points.visible = this.visible;

    // Position the cloud using the camera transform
    // The domain cloud is in camera-local frame (meters).
    // We need to:
    //   1. Scale from meters to world units
    //   2. Transform from camera frame to world frame
    // For now, apply the world→camera inverse as the object's matrix.
    const S = WORLD.SCALE_FACTOR;
    const threeMatrix = domainPoseToThreeMatrix(this.worldToCamera, S);
    this.points.matrixAutoUpdate = false;
    this.points.matrix.copy(threeMatrix);

    this.scene.add(this.points);
  }

  // ─── Voxel map ────────────────────────────────

  private updateVoxelMap(voxels: readonly import('@/types/depth').VoxelEntry[]): void {
    if (this.mapPoints) {
      this.scene.remove(this.mapPoints);
      this.mapPoints.geometry.dispose();
      (this.mapPoints.material as THREE.Material).dispose();
      this.mapPoints = null;
    }

    if (voxels.length === 0) return;

    const positions = new Float32Array(voxels.length * 3);
    const colors = new Float32Array(voxels.length * 3);

    for (let i = 0; i < voxels.length; i++) {
      const off = i * 3;
      positions[off] = voxels[i].x;
      positions[off + 1] = voxels[i].y;
      positions[off + 2] = voxels[i].z;
      colors[off] = voxels[i].r;
      colors[off + 1] = voxels[i].g;
      colors[off + 2] = voxels[i].b;
    }

    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));

    const material = new THREE.PointsMaterial({
      size: DEPTH_OVERLAY_RENDER.pointSizePx,
      sizeAttenuation: false,
      vertexColors: true,
      depthTest: true,
      depthWrite: true,
      opacity: 0.9,
      transparent: true,
    });

    this.mapPoints = new THREE.Points(geometry, material);
    this.mapPoints.name = 'depth-cloud-map';
    this.scene.add(this.mapPoints);
  }

  private clearVoxelMap(): void {
    if (this.mapPoints) {
      this.scene.remove(this.mapPoints);
      this.mapPoints.geometry.dispose();
      (this.mapPoints.material as THREE.Material).dispose();
      this.mapPoints = null;
    }
  }

  // ─── Dispose ──────────────────────────────────

  dispose(): void {
    if (this.points) {
      this.scene.remove(this.points);
      this.points.geometry.dispose();
      (this.points.material as THREE.Material).dispose();
    }
    this.clearVoxelMap();
  }
}
