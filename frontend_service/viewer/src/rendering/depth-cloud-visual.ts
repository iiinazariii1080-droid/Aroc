/**
 * depth-cloud-visual.ts — Three.js point cloud renderer for depth frames.
 *
 * Rendering layer. Takes world-frame PointCloud data and renders
 * it in the Three.js scene.
 *
 * Key fix from audit: sizeAttenuation = false, point size in screen pixels.
 * This eliminates the pixel shift bug when changing camera distance.
 */

import * as THREE from 'three';
import type { EventBus } from '@/event-bus';
import type { PointCloud } from '@/types/depth';
import { DEPTH_OVERLAY_RENDER, DEPTH_OVERLAY, VOXEL_RECORDING } from '@/config/scene-config';

const DEFAULT_POINT_SIZE = 0.1;
const POINT_SIZE_SCALE_PX = 100;

/**
 * Maximum points the live cloud geometry can hold.
 * Pre-allocated to avoid per-frame GC on Raspberry Pi.
 * Based on: (640/stride) × (480/stride). At stride=4: 160×120 = 19200.
 */
const MAX_LIVE_POINTS = Math.ceil(640 / DEPTH_OVERLAY.stridePx) *
                        Math.ceil(480 / DEPTH_OVERLAY.stridePx);

export class DepthCloudVisual {
  private readonly scene: THREE.Scene;
  private readonly bus: EventBus;

  /** Points object for the live depth cloud (reused). */
  private points: THREE.Points | null = null;

  /** Pre-allocated geometry (reused per frame to avoid GC). */
  private liveGeometry: THREE.BufferGeometry | null = null;

  /** Points object for the accumulated voxel map (pre-allocated). */
  private mapPoints: THREE.Points | null = null;

  /** Pre-allocated geometry for the voxel map (reused). */
  private mapGeometry: THREE.BufferGeometry | null = null;

  /** Whether depth cloud is visible. */
  private visible = false;
  private pointSizeNorm = DEFAULT_POINT_SIZE;

  constructor(scene: THREE.Scene, bus: EventBus) {
    this.scene = scene;
    this.bus = bus;

    // Pre-allocate live cloud geometry + points once
    this.initLiveCloud();

    // Wire events
    this.bus.on('depth:cloud', (cloud) => {
      if (this.visible) this.updateCloud(cloud);
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

    this.bus.on('ui:clearFrame', () => {
      this.clearLiveCloud();
    });

    this.bus.on('ui:pointSize', (size) => {
      this.setPointSize(size);
    });
  }

  // ─── Live cloud (pre-allocated) ───────────────

  private initLiveCloud(): void {
    this.liveGeometry = new THREE.BufferGeometry();
    const posArr = new Float32Array(MAX_LIVE_POINTS * 3);
    const colArr = new Float32Array(MAX_LIVE_POINTS * 3);
    this.liveGeometry.setAttribute('position', new THREE.BufferAttribute(posArr, 3));
    this.liveGeometry.setAttribute('color', new THREE.BufferAttribute(colArr, 3));
    this.liveGeometry.setDrawRange(0, 0); // nothing visible initially

    const material = new THREE.PointsMaterial({
      size: this.toPointSizePx(this.pointSizeNorm),
      sizeAttenuation: false,
      vertexColors: true,
      depthTest: DEPTH_OVERLAY_RENDER.depthTest,
      depthWrite: DEPTH_OVERLAY_RENDER.depthWrite,
      opacity: 1,
      transparent: false,
    });
    material.depthFunc = THREE.LessEqualDepth;

    this.points = new THREE.Points(this.liveGeometry, material);
    this.points.name = 'depth-cloud-live';
    this.points.visible = this.visible;
    this.points.frustumCulled = false;
    this.points.renderOrder = 2;
    this.scene.add(this.points);
  }

  private updateCloud(cloud: PointCloud): void {
    if (!this.liveGeometry || !this.points) return;
    if (cloud.count === 0) {
      this.liveGeometry.setDrawRange(0, 0);
      return;
    }

    // Copy domain data into pre-allocated buffers (no new allocation)
    const posAttr = this.liveGeometry.getAttribute('position') as THREE.BufferAttribute;
    const colAttr = this.liveGeometry.getAttribute('color') as THREE.BufferAttribute;
    const count = Math.min(cloud.count, MAX_LIVE_POINTS);

    (posAttr.array as Float32Array).set(cloud.positions.subarray(0, count * 3));
    posAttr.needsUpdate = true;

    (colAttr.array as Float32Array).set(cloud.colors.subarray(0, count * 3));
    colAttr.needsUpdate = true;

    this.liveGeometry.setDrawRange(0, count);
  }

  private clearLiveCloud(): void {
    if (this.liveGeometry) {
      this.liveGeometry.setDrawRange(0, 0);
    }
  }

  // ─── Voxel map (pre-allocated) ──────────────────

  private initVoxelMap(): void {
    const maxVoxels = VOXEL_RECORDING.maxVoxels;
    this.mapGeometry = new THREE.BufferGeometry();
    const posArr = new Float32Array(maxVoxels * 3);
    const colArr = new Float32Array(maxVoxels * 3);
    this.mapGeometry.setAttribute('position', new THREE.BufferAttribute(posArr, 3));
    this.mapGeometry.setAttribute('color', new THREE.BufferAttribute(colArr, 3));
    this.mapGeometry.setDrawRange(0, 0);

    const material = new THREE.PointsMaterial({
      size: this.toPointSizePx(this.pointSizeNorm),
      sizeAttenuation: false,
      vertexColors: true,
      depthTest: true,
      depthWrite: true,
      opacity: VOXEL_RECORDING.mapOpacity,
      transparent: VOXEL_RECORDING.mapOpacity < 1,
    });
    material.depthFunc = THREE.LessEqualDepth;

    this.mapPoints = new THREE.Points(this.mapGeometry, material);
    this.mapPoints.name = 'depth-cloud-map';
    this.mapPoints.frustumCulled = false;
    this.mapPoints.renderOrder = 1;
    this.scene.add(this.mapPoints);
  }

  private setPointSize(size: number): void {
    const clamped = Math.max(0.01, Math.min(0.1, size));
    this.pointSizeNorm = clamped;
    const sizePx = this.toPointSizePx(clamped);

    if (this.points?.material instanceof THREE.PointsMaterial) {
      this.points.material.size = sizePx;
      this.points.material.needsUpdate = true;
    }

    if (this.mapPoints?.material instanceof THREE.PointsMaterial) {
      this.mapPoints.material.size = sizePx;
      this.mapPoints.material.needsUpdate = true;
    }
  }

  private toPointSizePx(normSize: number): number {
    return Math.max(1, normSize * POINT_SIZE_SCALE_PX);
  }

  private updateVoxelMap(voxels: readonly import('@/types/depth').VoxelEntry[]): void {
    if (!this.mapGeometry) this.initVoxelMap();
    if (!this.mapGeometry || !this.mapPoints) return;

    const count = Math.min(voxels.length, VOXEL_RECORDING.maxVoxels);

    if (count === 0) {
      this.mapGeometry.setDrawRange(0, 0);
      return;
    }

    // Copy voxel data into pre-allocated buffers (no new allocation)
    const posAttr = this.mapGeometry.getAttribute('position') as THREE.BufferAttribute;
    const colAttr = this.mapGeometry.getAttribute('color') as THREE.BufferAttribute;
    const posArr = posAttr.array as Float32Array;
    const colArr = colAttr.array as Float32Array;

    for (let i = 0; i < count; i++) {
      const off = i * 3;
      posArr[off]     = voxels[i].x;
      posArr[off + 1] = voxels[i].y;
      posArr[off + 2] = voxels[i].z;
      colArr[off]     = voxels[i].r;
      colArr[off + 1] = voxels[i].g;
      colArr[off + 2] = voxels[i].b;
    }

    posAttr.needsUpdate = true;
    colAttr.needsUpdate = true;
    this.mapGeometry.setDrawRange(0, count);
  }

  private clearVoxelMap(): void {
    if (this.mapGeometry) {
      this.mapGeometry.setDrawRange(0, 0);
    }
  }

  // ─── Dispose ──────────────────────────────────

  dispose(): void {
    if (this.points) {
      this.scene.remove(this.points);
      (this.points.material as THREE.Material).dispose();
    }
    if (this.liveGeometry) {
      this.liveGeometry.dispose();
    }
    if (this.mapPoints) {
      this.scene.remove(this.mapPoints);
      (this.mapPoints.material as THREE.Material).dispose();
    }
    if (this.mapGeometry) {
      this.mapGeometry.dispose();
    }
  }
}
