/**
 * depth-api.ts — HTTP client for depth camera endpoints.
 *
 * Data layer. Fetches depth frames and depth maps from the backend API.
 */

import type { EventBus } from '@/event-bus';
import type { DepthFrame } from '@/types/depth';
import { API, DEPTH_CAMERA, DEPTH_OVERLAY } from '@/config/scene-config';
import { processDepthFrame } from '@/domain/depth-processor';

export interface DepthApiOptions {
  bus: EventBus;
  /** Polling interval in ms. 0 = no polling. */
  pollIntervalMs?: number;
}

export class DepthApi {
  private readonly bus: EventBus;
  private timer: ReturnType<typeof setInterval> | null = null;
  private inFlight = false;
  private disposed = false;

  constructor(opts: DepthApiOptions) {
    this.bus = opts.bus;

    if (opts.pollIntervalMs && opts.pollIntervalMs > 0) {
      this.startPolling(opts.pollIntervalMs);
    }
  }

  // ─── Polling ──────────────────────────────────

  startPolling(intervalMs: number): void {
    this.stopPolling();
    this.timer = setInterval(() => this.fetchFrame(), intervalMs);
  }

  stopPolling(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  // ─── Fetch depth frame ────────────────────────

  async fetchFrame(): Promise<void> {
    if (this.inFlight || this.disposed) return;
    this.inFlight = true;

    try {
      const resp = await fetch(API.depthFrameColorOverlay);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

      const json = await resp.json();

      // Parse depth data
      const depthRaw = this.parseDepthArray(json.depth, json.width, json.height);
      const rgbRaw = json.color ? this.parseRgbArray(json.color, json.width, json.height) : null;

      // Emit raw frame
      const depthFrame: DepthFrame = {
        raw: depthRaw,
        width: json.width,
        height: json.height,
        timestamp: Date.now(),
      };
      this.bus.emit('depth:frame', depthFrame);

      // Process into point cloud in domain layer
      const cloud = processDepthFrame(
        depthRaw, rgbRaw,
        json.width, json.height,
        DEPTH_CAMERA.intrinsics,
        DEPTH_CAMERA.depthCalibration,
        DEPTH_OVERLAY,
      );

      this.bus.emit('depth:cloud', cloud);

    } catch (err) {
      this.bus.emit('error', {
        source: 'DepthApi',
        message: 'Failed to fetch depth frame',
        detail: err,
      });
    } finally {
      this.inFlight = false;
    }
  }

  // ─── Depth map save/load ──────────────────────

  async saveDepthMap(data: ArrayBuffer): Promise<void> {
    try {
      await fetch(API.depthMap.save, {
        method: 'POST',
        headers: { 'Content-Type': 'application/octet-stream' },
        body: data,
      });
    } catch (err) {
      this.bus.emit('error', { source: 'DepthApi', message: 'Failed to save depth map', detail: err });
    }
  }

  async loadDepthMap(): Promise<ArrayBuffer | null> {
    try {
      const resp = await fetch(API.depthMap.load);
      if (!resp.ok) return null;
      return await resp.arrayBuffer();
    } catch (err) {
      this.bus.emit('error', { source: 'DepthApi', message: 'Failed to load depth map', detail: err });
      return null;
    }
  }

  // ─── Parsers ──────────────────────────────────

  private parseDepthArray(data: number[] | string, w: number, h: number): Uint16Array {
    if (Array.isArray(data)) {
      return new Uint16Array(data);
    }
    // Base64 encoded
    if (typeof data === 'string') {
      const binary = atob(data);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
      return new Uint16Array(bytes.buffer);
    }
    return new Uint16Array(w * h);
  }

  private parseRgbArray(data: number[] | string, w: number, h: number): Uint8Array {
    if (Array.isArray(data)) {
      return new Uint8Array(data);
    }
    if (typeof data === 'string') {
      const binary = atob(data);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
      return bytes;
    }
    return new Uint8Array(w * h * 3);
  }

  // ─── Dispose ──────────────────────────────────

  dispose(): void {
    this.disposed = true;
    this.stopPolling();
  }
}
