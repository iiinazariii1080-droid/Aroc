/**
 * depth-api.ts — HTTP client for depth camera endpoints.
 *
 * Data layer. Fetches depth frames and depth maps from the backend API.
 * Emits raw data events; domain processing is handled by the orchestrator.
 */

import type { EventBus } from '@/event-bus';
import { API, VOXEL_RECORDING } from '@/config/scene-config';
import { parseBase64Uint16, parseBase64Uint8 } from '@/domain/binary-parsers';
import { encodeDMP1, decodeDMP1 } from '@/domain/depth-codec';
import {
  validateDepthPayload,
  validateRgbPayload,
  isReasonableTimestamp,
} from '@/domain/validators';
import type { VoxelEntry } from '@/types/depth';

/** Fetch timeout in ms — prevents inFlight from locking permanently. */
const FETCH_TIMEOUT_MS = 8_000;

export interface DepthApiOptions {
  bus: EventBus;
  /** Polling interval in ms. 0 = no polling. */
  pollIntervalMs?: number;
}

export class DepthApi {
  private readonly bus: EventBus;
  private readonly unsubFetchNow: () => void;
  private timer: ReturnType<typeof setInterval> | null = null;
  private inFlight = false;
  private disposed = false;

  constructor(opts: DepthApiOptions) {
    this.bus = opts.bus;
    this.unsubFetchNow = this.bus.on('depth:fetchNow', () => {
      void this.fetchFrame();
    });

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

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);

    try {
      const resp = await fetch(API.depthFrameColorOverlay, { signal: controller.signal });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

      const json = await resp.json();

      // Schema v2 uses depth_data/rgb_data/mapped_color; v1 used depth/color/data
      const depthAlias = json.depth_data != null ? 'depth_data' : json.depth != null ? 'depth' : 'data';
      const colorAlias = json.mapped_color != null ? 'mapped_color' : json.rgb_data != null ? 'rgb_data' : 'color';
      const depthField = json.depth_data ?? json.depth ?? json.data;
      const colorField = json.mapped_color ?? json.rgb_data ?? json.color;
      const w = Number(json.width) || 0;
      const h = Number(json.height) || 0;

      if (!depthField || !w || !h) {
        throw new Error('Depth response missing depth_data or dimensions');
      }

      const depthRaw = parseBase64Uint16(depthField, w * h);
      const rgbRaw = colorField ? parseBase64Uint8(colorField, w * h * 3) : null;

      const depthValid = validateDepthPayload(depthRaw, w, h);
      if (!depthValid.ok) {
        this.bus.emit('depth:dropped', { reason: 'validation', detail: depthValid.reason });
        throw new Error(depthValid.reason);
      }

      const rgbValid = validateRgbPayload(rgbRaw, w, h);
      if (!rgbValid.ok) {
        this.bus.emit('depth:dropped', { reason: 'validation', detail: rgbValid.reason });
        throw new Error(rgbValid.reason);
      }

      const sourceTs = Number(
        json.depth_capture_ts_ms
        ?? json.timestamp_ms
        ?? json.timestamp
        ?? json.ts_ms
        ?? json.ts,
      );
      const timestampSource = isReasonableTimestamp(sourceTs) ? 'source' : 'local';
      const timestamp = timestampSource === 'source' ? sourceTs : Date.now();

      console.debug('[DepthApi] frame schema', {
        depthAlias,
        colorAlias: colorField ? colorAlias : null,
        width: w,
        height: h,
        timestampSource,
      });

      // Emit raw frame — orchestrator handles domain processing
      this.bus.emit('raw:depthFrame', {
        depthRaw,
        rgbRaw,
        width: w,
        height: h,
        timestamp,
        timestampSource,
      });

    } catch (err) {
      this.bus.emit('error', {
        source: 'DepthApi',
        message: 'Failed to fetch depth frame',
        detail: err,
      });
    } finally {
      clearTimeout(timeout);
      this.inFlight = false;
    }
  }

  // ─── Depth map load ────────────────────────────

  async saveVoxelMap(voxels: readonly VoxelEntry[]): Promise<{ ok: boolean; message: string }> {
    if (voxels.length === 0) {
      return { ok: false, message: 'Nothing to save (map empty)' };
    }

    try {
      const body = encodeDMP1(voxels);
      const resp = await fetch(API.depthMap.save, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/octet-stream',
          'X-Depth-Map-Voxel-Size-Mm': String(VOXEL_RECORDING.voxelSizeMm),
        },
        body,
      });

      if (!resp.ok) {
        const detail = await resp.text().catch(() => '');
        return { ok: false, message: `Save failed: ${resp.status} ${detail}`.trim() };
      }

      return { ok: true, message: `Saved ${voxels.length} voxels` };
    } catch (err) {
      this.bus.emit('error', { source: 'DepthApi', message: 'Failed to save depth map', detail: err });
      return { ok: false, message: `Save error: ${String(err)}` };
    }
  }

  async loadVoxelMap(): Promise<{ ok: boolean; message: string; voxels: VoxelEntry[]; version?: number }> {
    try {
      const resp = await fetch(API.depthMap.load);
      if (resp.status === 404) {
        return { ok: false, message: 'No saved map on server', voxels: [] };
      }
      if (!resp.ok) {
        return { ok: false, message: `Load failed: ${resp.status}`, voxels: [] };
      }

      const buffer = await resp.arrayBuffer();
      const result = decodeDMP1(buffer);
      return {
        ok: true,
        message: `Loaded ${result.voxels.length} voxels (v${result.version})`,
        voxels: result.voxels,
        version: result.version,
      };
    } catch (err) {
      this.bus.emit('error', { source: 'DepthApi', message: 'Failed to load depth map', detail: err });
      return { ok: false, message: `Load error: ${String(err)}`, voxels: [] };
    }
  }

  async deleteVoxelMap(): Promise<{ ok: boolean; message: string }> {
    try {
      const resp = await fetch(API.depthMap.delete, { method: 'DELETE' });
      if (!resp.ok) {
        return { ok: false, message: `Delete failed: ${resp.status}` };
      }
      return { ok: true, message: 'Deleted from server' };
    } catch (err) {
      this.bus.emit('error', { source: 'DepthApi', message: 'Failed to delete depth map', detail: err });
      return { ok: false, message: `Delete error: ${String(err)}` };
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

  // ─── Dispose ──────────────────────────────────

  dispose(): void {
    this.disposed = true;
    this.stopPolling();
    this.unsubFetchNow();
  }
}
