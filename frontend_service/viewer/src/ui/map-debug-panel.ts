/**
 * map-debug-panel.ts — Debug panel with 6 sliders for AGV map placement.
 *
 * UI layer. Provides range sliders for position (X/Y/Z) and rotation (RX/RY/RZ)
 * of the map pivot group. Activated when AGV map is shown (?agv_map).
 */

import type { AgvMapVisual } from '@/rendering/agv-map-visual';

// ─── Constants ─────────────────────────────────────

const POS_MIN = -600;
const POS_MAX = 600;
const POS_STEP = 0.5;
const DEG_MIN = -180;
const DEG_MAX = 180;
const DEG_STEP = 1;

const PANEL_CSS = [
  'position:fixed',
  'right:12px',
  'bottom:12px',
  'padding:10px 14px',
  'border-radius:8px',
  'background:rgba(0,0,0,0.72)',
  'color:#e2e8f0',
  'font-family:monospace',
  'font-size:11px',
  'line-height:1.6',
  'z-index:15',
  'min-width:260px',
].join(';');

const ROW_CSS = 'display:flex;align-items:center;gap:6px;margin:2px 0';
const LABEL_CSS = 'width:22px;text-align:right;color:#94a3b8';
const RANGE_CSS = 'flex:1;accent-color:#f59e0b;cursor:pointer';
const VALUE_CSS = 'width:54px;text-align:left;color:#fef08a';

// ─── Component ─────────────────────────────────────

export interface MapDebugPanelOptions {
  mapVisual: AgvMapVisual;
  /** Callback to disable orbit controls while dragging sliders. */
  onPointerDown?: () => void;
  onPointerUp?: () => void;
}

export class MapDebugPanel {
  private el: HTMLDivElement | null = null;
  private readonly mapVisual: AgvMapVisual;

  private pos: [number, number, number] = [0, 0, 0];
  private rot: [number, number, number] = [0, 0, 0];

  private posInputs: HTMLInputElement[] = [];
  private posLabels: HTMLSpanElement[] = [];
  private rotInputs: HTMLInputElement[] = [];
  private rotLabels: HTMLSpanElement[] = [];
  private packedEl: HTMLSpanElement | null = null;

  constructor(opts: MapDebugPanelOptions) {
    this.mapVisual = opts.mapVisual;
    this.createPanel(opts.onPointerDown, opts.onPointerUp);
    this.apply();
    this.updateView();
  }

  // ─── Panel creation ──────────────────────────

  private createPanel(onDown?: () => void, onUp?: () => void): void {
    this.el = document.createElement('div');
    this.el.id = 'arm3d-map-debug-panel';
    this.el.style.cssText = PANEL_CSS;

    // Pause orbit controls on interaction
    if (onDown) this.el.addEventListener('pointerdown', onDown);
    if (onUp) {
      this.el.addEventListener('pointerup', onUp);
      this.el.addEventListener('pointercancel', onUp);
      this.el.addEventListener('mouseleave', (e) => { if (!(e.buttons & 1)) onUp(); });
    }

    // Title
    const title = document.createElement('div');
    title.textContent = '🗺 Map Transform';
    title.style.fontWeight = 'bold';
    title.style.marginBottom = '6px';
    this.el.appendChild(title);

    // Position sliders
    const posHeader = document.createElement('div');
    posHeader.textContent = 'Position (wu)';
    posHeader.style.color = '#94a3b8';
    this.el.appendChild(posHeader);

    const axes = ['X', 'Y', 'Z'] as const;
    for (let i = 0; i < 3; i++) {
      const { input, label } = this.createSliderRow(
        `P${axes[i]}`, POS_MIN, POS_MAX, POS_STEP, this.pos[i],
        (v) => { this.pos[i] = v; this.onChanged(); },
      );
      this.posInputs.push(input);
      this.posLabels.push(label);
      this.el.appendChild(input.parentElement!);
    }

    // Rotation sliders
    const rotHeader = document.createElement('div');
    rotHeader.textContent = 'Rotation (deg)';
    rotHeader.style.cssText = 'color:#94a3b8;margin-top:6px';
    this.el.appendChild(rotHeader);

    for (let i = 0; i < 3; i++) {
      const { input, label } = this.createSliderRow(
        `R${axes[i]}`, DEG_MIN, DEG_MAX, DEG_STEP, this.rot[i],
        (v) => { this.rot[i] = Math.round(v); this.onChanged(); },
      );
      this.rotInputs.push(input);
      this.rotLabels.push(label);
      this.el.appendChild(input.parentElement!);
    }

    // Packed summary
    this.packedEl = document.createElement('span');
    this.packedEl.style.cssText = 'display:block;margin-top:4px;color:#64748b;font-size:10px';
    this.el.appendChild(this.packedEl);

    // Reset button
    const resetBtn = document.createElement('button');
    resetBtn.textContent = '↺ Reset';
    resetBtn.style.cssText =
      'margin-top:6px;padding:3px 10px;border:1px solid #555;border-radius:4px;' +
      'cursor:pointer;font-size:11px;background:#1a1a2e;color:#e2e8f0';
    resetBtn.addEventListener('click', () => this.reset());
    this.el.appendChild(resetBtn);

    document.body.appendChild(this.el);
  }

  private createSliderRow(
    label: string, min: number, max: number, step: number, value: number,
    onChange: (v: number) => void,
  ): { input: HTMLInputElement; label: HTMLSpanElement } {
    const row = document.createElement('div');
    row.style.cssText = ROW_CSS;

    const lbl = document.createElement('span');
    lbl.style.cssText = LABEL_CSS;
    lbl.textContent = label;
    row.appendChild(lbl);

    const input = document.createElement('input');
    input.type = 'range';
    input.min = String(min);
    input.max = String(max);
    input.step = String(step);
    input.value = String(value);
    input.style.cssText = RANGE_CSS;
    row.appendChild(input);

    const valSpan = document.createElement('span');
    valSpan.style.cssText = VALUE_CSS;
    row.appendChild(valSpan);

    input.addEventListener('input', () => {
      onChange(Number(input.value));
    });

    // Attach row to input for easy DOM insertion
    Object.defineProperty(input, 'parentElement', {
      get() { return row; },
      configurable: true,
    });

    return { input, label: valSpan };
  }

  // ─── Logic ───────────────────────────────────

  private onChanged(): void {
    this.apply();
    this.updateView();
  }

  private apply(): void {
    this.mapVisual.setDebugTransform(this.pos, this.rot);
  }

  private updateView(): void {
    for (let i = 0; i < 3; i++) {
      if (this.posInputs[i]) this.posInputs[i].value = String(this.pos[i]);
      if (this.posLabels[i]) this.posLabels[i].textContent = this.pos[i].toFixed(1);
      if (this.rotInputs[i]) this.rotInputs[i].value = String(this.rot[i]);
      if (this.rotLabels[i]) this.rotLabels[i].textContent = `${Math.round(this.rot[i])}°`;
    }
    if (this.packedEl) {
      const p = this.pos.map((v) => v.toFixed(1));
      const r = this.rot.map((v) => Math.round(v));
      this.packedEl.textContent = `P ${p.join('/')}  R ${r.join('/')}`;
    }
  }

  private reset(): void {
    this.pos = [0, 0, 0];
    this.rot = [0, 0, 0];
    this.onChanged();
  }

  dispose(): void {
    if (this.el?.parentNode) {
      this.el.parentNode.removeChild(this.el);
    }
    this.el = null;
    this.posInputs = [];
    this.posLabels = [];
    this.rotInputs = [];
    this.rotLabels = [];
    this.packedEl = null;
  }
}
