/**
 * cloud-controls.ts — Depth cloud capture & persistence panel.
 *
 * Full remake — clean separation of concerns:
 *
 *   UI (this file):
 *     - Renders buttons: Shot / Shot Color / Record / Clear Map / Clear Frame
 *     - Renders persist: Save / Load / Delete
 *     - Emits bus events for each action — no domain logic here
 *     - Owns save/load/delete HTTP calls (thin persistence layer)
 *
 *   Orchestrator (orchestrator.ts):
 *     - Owns VoxelMap state
 *     - Handles ui:shot, ui:shotColor, ui:toggleRecording, voxel:clear,
 *       ui:clearFrame, voxel:load
 *     - Emits voxel:updated after every mutation
 *
 * Buttons:
 *   📷 Shot       — add last depth cloud to voxel map (depth-only color)
 *   🎨 Shot Color — fetch fresh frame with RGB overlay, add to voxel map
 *   ⏺ Record     — continuous recording toggle
 *   🗑 Clear map   — clear all voxels
 *   ✕ Clear frame — erase voxels in current camera frustum
 *   💾 Save       — encode VoxelMap → DMP1 V2, POST to server
 *   📂 Load       — GET from server, decode, replace VoxelMap
 *   🗑 Delete     — DELETE on server + clear local map
 */

import type { EventBus } from '@/event-bus';
import type { VoxelEntry } from '@/types/depth';

// ─── Styles ────────────────────────────────────────

const PANEL_CSS = [
  'position:fixed',
  'right:12px',
  'top:12px',
  'padding:10px 14px',
  'border-radius:8px',
  'background:rgba(0,0,0,0.72)',
  'color:#e2e8f0',
  'font-family:monospace',
  'font-size:11px',
  'line-height:1.6',
  'z-index:14',
  'user-select:none',
  'min-width:180px',
].join(';');

const BTN_CSS = [
  'display:inline-block',
  'margin:2px 3px',
  'padding:3px 8px',
  'border:1px solid #555',
  'border-radius:4px',
  'cursor:pointer',
  'font-size:11px',
  'background:#1a1a2e',
  'color:#e2e8f0',
].join(';');

// ─── Component ─────────────────────────────────────

export interface CloudControlsOptions {
  bus: EventBus;
  /** Callback to retrieve current voxel map entries for save. */
  getVoxels: () => readonly VoxelEntry[];
  saveVoxelMap: (voxels: readonly VoxelEntry[]) => Promise<{ ok: boolean; message: string }>;
  loadVoxelMap: () => Promise<{ ok: boolean; message: string; voxels: VoxelEntry[] }>;
  deleteVoxelMap: () => Promise<{ ok: boolean; message: string }>;
}

export class CloudControls {
  private static readonly POINT_SIZE_MIN = 0.01;
  private static readonly POINT_SIZE_MAX = 0.1;
  private static readonly POINT_SIZE_STEP = 0.005;
  private static readonly POINT_SIZE_DEFAULT = 0.1;

  private el: HTMLDivElement | null = null;
  private readonly bus: EventBus;
  private readonly getVoxels: () => readonly VoxelEntry[];
  private readonly saveVoxelMap: (voxels: readonly VoxelEntry[]) => Promise<{ ok: boolean; message: string }>;
  private readonly loadVoxelMap: () => Promise<{ ok: boolean; message: string; voxels: VoxelEntry[] }>;
  private readonly deleteVoxelMap: () => Promise<{ ok: boolean; message: string }>;

  private recording = false;
  private voxelCount = 0;
  private lastStatus = 'idle';
  private dropCount = 0;
  private mapMismatchCount = 0;
  private lastSkewMs: number | null = null;
  private lastDropReason = '-';

  private btnRecord: HTMLButtonElement | null = null;
  private statusEl: HTMLSpanElement | null = null;
  private persistEl: HTMLSpanElement | null = null;
  private pointSizeInput: HTMLInputElement | null = null;
  private pointSizeValueEl: HTMLSpanElement | null = null;

  constructor(opts: CloudControlsOptions) {
    this.bus = opts.bus;
    this.getVoxels = opts.getVoxels;
    this.saveVoxelMap = opts.saveVoxelMap;
    this.loadVoxelMap = opts.loadVoxelMap;
    this.deleteVoxelMap = opts.deleteVoxelMap;

    this.createPanel();
    this.wireEvents();
  }

  // ─── Panel creation ──────────────────────────

  private createPanel(): void {
    this.el = document.createElement('div');
    this.el.id = 'arm3d-cloud-controls';
    this.el.style.cssText = PANEL_CSS;

    // Title
    const title = document.createElement('div');
    title.textContent = '☁ Depth Cloud';
    title.style.fontWeight = 'bold';
    title.style.marginBottom = '6px';
    this.el.appendChild(title);

    // Status line
    this.statusEl = document.createElement('span');
    this.statusEl.style.display = 'block';
    this.statusEl.textContent = 'Map: 0 voxels';
    this.el.appendChild(this.statusEl);

    // ── Row 1: Shot / Shot Color / Record ──
    const row1 = document.createElement('div');
    row1.style.marginTop = '6px';

    row1.appendChild(this.makeBtn('📷 Shot', () => this.onShot()));
    row1.appendChild(this.makeBtn('🎨 Shot Color', () => this.onShotColor()));
    this.btnRecord = this.makeBtn('⏺ Record', () => this.onToggleRecord());
    row1.appendChild(this.btnRecord);
    this.el.appendChild(row1);

    // ── Row 2: Clear map / Clear frame ──
    const row2 = document.createElement('div');
    row2.style.marginTop = '4px';

    row2.appendChild(this.makeBtn('🗑 Clear map', () => this.onClearMap()));
    row2.appendChild(this.makeBtn('✕ Clear frame', () => this.onClearFrame()));
    this.el.appendChild(row2);

    // ── Row 3: Save / Load / Delete ──
    const row3 = document.createElement('div');
    row3.style.marginTop = '4px';

    row3.appendChild(this.makeBtn('💾 Save', () => this.onSave()));
    row3.appendChild(this.makeBtn('📂 Load', () => this.onLoad()));
    row3.appendChild(this.makeBtn('🗑 Delete', () => this.onDelete()));
    this.el.appendChild(row3);

    // ── Row 4: Point size ──
    const row4 = document.createElement('div');
    row4.style.cssText = 'margin-top:6px;display:flex;align-items:center;gap:6px';

    const sizeLabel = document.createElement('span');
    sizeLabel.textContent = 'Point size';
    sizeLabel.style.cssText = 'color:#94a3b8;min-width:62px';
    row4.appendChild(sizeLabel);

    this.pointSizeInput = document.createElement('input');
    this.pointSizeInput.type = 'range';
    this.pointSizeInput.min = String(CloudControls.POINT_SIZE_MIN);
    this.pointSizeInput.max = String(CloudControls.POINT_SIZE_MAX);
    this.pointSizeInput.step = String(CloudControls.POINT_SIZE_STEP);
    this.pointSizeInput.value = String(CloudControls.POINT_SIZE_DEFAULT);
    this.pointSizeInput.style.cssText = 'flex:1;accent-color:#3b82f6;cursor:pointer';
    this.pointSizeInput.addEventListener('input', () => this.onPointSizeChanged());
    row4.appendChild(this.pointSizeInput);

    this.pointSizeValueEl = document.createElement('span');
    this.pointSizeValueEl.style.cssText = 'width:38px;color:#a5f3c4;text-align:right';
    this.pointSizeValueEl.textContent = CloudControls.POINT_SIZE_DEFAULT.toFixed(3);
    row4.appendChild(this.pointSizeValueEl);

    this.el.appendChild(row4);

    // Persistence status line
    this.persistEl = document.createElement('span');
    this.persistEl.style.display = 'block';
    this.persistEl.style.marginTop = '4px';
    this.persistEl.style.color = '#94a3b8';
    this.el.appendChild(this.persistEl);

    document.body.appendChild(this.el);
    this.bus.emit('ui:pointSize', CloudControls.POINT_SIZE_DEFAULT);
  }

  private makeBtn(label: string, onClick: () => void): HTMLButtonElement {
    const btn = document.createElement('button');
    btn.style.cssText = BTN_CSS;
    btn.textContent = label;
    btn.addEventListener('click', onClick);
    return btn;
  }

  // ─── Event wiring ───────────────────────────

  private wireEvents(): void {
    // Track voxel count from orchestrator updates
    this.bus.on('voxel:updated', (voxels) => {
      this.voxelCount = voxels.length;
      this.refreshStatus();
    });

    this.bus.on('depth:skew', (payload) => {
      this.lastSkewMs = payload.skewMs;
      this.refreshStatus();
    });

    this.bus.on('depth:dropped', (payload) => {
      this.dropCount += 1;
      if (payload.reason === 'map_id_mismatch') {
        this.mapMismatchCount += 1;
      }
      this.lastDropReason = payload.reason;
      this.refreshStatus();
    });
  }

  // ─── Button handlers ────────────────────────

  private onShot(): void {
    this.bus.emit('ui:shot');
    this.setStatus('shot sent');
  }

  private onShotColor(): void {
    this.bus.emit('ui:shotColor');
    this.setStatus('shot color sent');
  }

  private onToggleRecord(): void {
    this.recording = !this.recording;
    this.bus.emit('ui:toggleRecording', this.recording);
    if (this.btnRecord) {
      this.btnRecord.textContent = this.recording ? '⏸ Pause' : '⏺ Record';
      this.btnRecord.style.borderColor = this.recording ? '#ef4444' : '#555';
    }
    this.setStatus(this.recording ? 'recording' : 'paused');
  }

  private onClearMap(): void {
    this.bus.emit('voxel:clear');
    this.recording = false;
    if (this.btnRecord) {
      this.btnRecord.textContent = '⏺ Record';
      this.btnRecord.style.borderColor = '#555';
    }
    this.setStatus('map cleared');
  }

  private onClearFrame(): void {
    this.bus.emit('ui:clearFrame');
    this.setStatus('clear frame');
  }

  private onPointSizeChanged(): void {
    if (!this.pointSizeInput) return;
    const value = Number(this.pointSizeInput.value);
    const clamped = Math.max(CloudControls.POINT_SIZE_MIN, Math.min(CloudControls.POINT_SIZE_MAX, value));
    this.bus.emit('ui:pointSize', clamped);
    if (this.pointSizeValueEl) {
      this.pointSizeValueEl.textContent = clamped.toFixed(3);
    }
    this.setStatus(`point size ${clamped.toFixed(3)}`);
  }

  // ─── Save / Load / Delete ───────────────────

  private async onSave(): Promise<void> {
    const voxels = this.getVoxels();
    if (voxels.length === 0) {
      this.setPersist('Nothing to save (map empty)');
      return;
    }

    this.setPersist(`Saving ${voxels.length} voxels...`);
    const result = await this.saveVoxelMap(voxels);
    this.setPersist(result.ok ? `✓ ${result.message}` : `✗ ${result.message}`);
  }

  private async onLoad(): Promise<void> {
    this.setPersist('Loading...');
    const result = await this.loadVoxelMap();
    if (result.ok) {
      this.bus.emit('voxel:load', result.voxels);
      this.setPersist(`✓ ${result.message}`);
      return;
    }
    this.setPersist(`✗ ${result.message}`);
  }

  private async onDelete(): Promise<void> {
    this.setPersist('Deleting...');
    const result = await this.deleteVoxelMap();
    if (result.ok) {
      this.bus.emit('voxel:clear');
      this.setPersist('✓ Deleted from server + cleared map');
      return;
    }
    this.setPersist(`✗ ${result.message}`);
  }

  // ─── Display helpers ────────────────────────

  private setStatus(msg: string): void {
    this.lastStatus = msg;
    this.refreshStatus();
  }

  private refreshStatus(): void {
    if (!this.statusEl) return;
    const skewText = this.lastSkewMs == null ? 'n/a' : `${this.lastSkewMs.toFixed(0)}ms`;
    this.statusEl.textContent =
      `Map: ${this.voxelCount} | skew: ${skewText} | drop: ${this.dropCount} (map:${this.mapMismatchCount}, last:${this.lastDropReason}) | ${this.lastStatus}`;
  }

  private setPersist(msg: string): void {
    if (this.persistEl) this.persistEl.textContent = msg;
  }

  // ─── Public API ─────────────────────────────

  /** Update voxel count externally (e.g. from orchestrator). */
  updateStats(_fps: number, voxelCount: number): void {
    this.voxelCount = voxelCount;
    this.refreshStatus();
  }

  dispose(): void {
    if (this.el?.parentNode) {
      this.el.parentNode.removeChild(this.el);
    }
    this.el = null;
    this.btnRecord = null;
    this.statusEl = null;
    this.persistEl = null;
    this.pointSizeInput = null;
    this.pointSizeValueEl = null;
  }
}
