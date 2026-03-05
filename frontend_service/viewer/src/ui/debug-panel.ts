/**
 * debug-panel.ts — Debug rotation/position transform panel.
 *
 * UI layer. Provides range sliders for manual rotation (rx/ry/rz)
 * and position (px/py/pz) adjustments.
 *
 * Activated via `?transform` query param.
 */

import type { ArmVisual } from '@/rendering/arm-visual';
import type { EventBus } from '@/event-bus';
import { TRANSFORMS } from '@/config/scene-config';

// ─── Constants ─────────────────────────────────────

const DEG_MIN = -180;
const DEG_MAX = 180;
const POS_MIN = -30;
const POS_MAX = 30;
const POS_STEP = 0.1;
const CAM_POS_MIN = -0.5;
const CAM_POS_MAX = 0.5;
const CAM_POS_STEP = 0.001;
const CAM_ROT_STEP = 1;
const CAM_DEFAULT_RX_DEG = -90;
const CAM_DEFAULT_RY_DEG = 90;

const PANEL_CSS = [
  'position:fixed',
  'left:12px',
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
const RANGE_CSS = 'flex:1;accent-color:#3b82f6;cursor:pointer';
const VALUE_CSS = 'width:48px;text-align:left;color:#a5f3c4';

// ─── Helpers ───────────────────────────────────────

function clampDeg(v: number): number {
  return Math.max(DEG_MIN, Math.min(DEG_MAX, Math.round(v) || 0));
}

function clampPos(v: number): number {
  return Math.max(POS_MIN, Math.min(POS_MAX, Number(v) || 0));
}

// ─── Component ─────────────────────────────────────

export interface DebugPanelOptions {
  armVisual: ArmVisual;
  bus: EventBus;
  /** Initial rotation from URL params (deg), or null. */
  initRotation?: [number, number, number] | null;
  /** Initial position from URL params (wu), or null. */
  initPosition?: [number, number, number] | null;
  /** Callback to disable orbit controls while dragging sliders. */
  onPointerDown?: () => void;
  onPointerUp?: () => void;
}

export class DebugPanel {
  private el: HTMLDivElement | null = null;
  private readonly armVisual: ArmVisual;
  private readonly bus: EventBus;
  private rotation: [number, number, number] = [0, 0, 0];
  private position: [number, number, number] = [0, 0, 0];
  private camera = {
    tx: TRANSFORMS.flangeToCamera.translation.x,
    ty: TRANSFORMS.flangeToCamera.translation.y,
    tz: TRANSFORMS.flangeToCamera.translation.z,
    rxDeg: CAM_DEFAULT_RX_DEG,
    ryDeg: CAM_DEFAULT_RY_DEG,
    rzDeg: (TRANSFORMS.flangeToCamera.rotation.yaw * 180) / Math.PI,
  };

  private rotInputs: HTMLInputElement[] = [];
  private rotLabels: HTMLSpanElement[] = [];
  private posInputs: HTMLInputElement[] = [];
  private posLabels: HTMLSpanElement[] = [];
  private camInputs: HTMLInputElement[] = [];
  private camLabels: HTMLSpanElement[] = [];
  private packedEl: HTMLSpanElement | null = null;

  constructor(opts: DebugPanelOptions) {
    this.armVisual = opts.armVisual;
    this.bus = opts.bus;

    // Load from URL params (if provided)
    if (opts.initRotation) this.rotation = opts.initRotation.map(clampDeg) as [number, number, number];
    if (opts.initPosition) this.position = opts.initPosition.map(clampPos) as [number, number, number];

    this.createPanel(opts.onPointerDown, opts.onPointerUp);
    this.applyTransform();
    this.updateView();
  }

  // ─── Panel creation ──────────────────────────

  private createPanel(onDown?: () => void, onUp?: () => void): void {
    this.el = document.createElement('div');
    this.el.id = 'arm3d-debug-panel';
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
    title.textContent = '⚙ Debug Transform';
    title.style.fontWeight = 'bold';
    title.style.marginBottom = '6px';
    this.el.appendChild(title);

    // Rotation sliders
    const rotHeader = document.createElement('div');
    rotHeader.textContent = 'Rotation (deg)';
    rotHeader.style.color = '#94a3b8';
    this.el.appendChild(rotHeader);

    const axes = ['X', 'Y', 'Z'] as const;
    for (let i = 0; i < 3; i++) {
      const { input, label } = this.createSliderRow(
        `R${axes[i]}`, DEG_MIN, DEG_MAX, 1, this.rotation[i],
        (v) => { this.rotation[i] = clampDeg(v); this.onChanged(); },
      );
      this.rotInputs.push(input);
      this.rotLabels.push(label);
      this.el.appendChild(input.parentElement!);
    }

    // Position sliders
    const posHeader = document.createElement('div');
    posHeader.textContent = 'Position (wu)';
    posHeader.style.cssText = 'color:#94a3b8;margin-top:6px';
    this.el.appendChild(posHeader);

    for (let i = 0; i < 3; i++) {
      const { input, label } = this.createSliderRow(
        `P${axes[i]}`, POS_MIN, POS_MAX, POS_STEP, this.position[i],
        (v) => { this.position[i] = clampPos(v); this.onChanged(); },
      );
      this.posInputs.push(input);
      this.posLabels.push(label);
      this.el.appendChild(input.parentElement!);
    }

    // Camera transform sliders
    const camHeader = document.createElement('div');
    camHeader.textContent = 'Camera Transform (m / deg)';
    camHeader.style.cssText = 'color:#94a3b8;margin-top:6px';
    this.el.appendChild(camHeader);

    const camPosLabels = ['tX', 'tY', 'tZ'] as const;
    const camPosVals = [this.camera.tx, this.camera.ty, this.camera.tz] as const;
    for (let i = 0; i < 3; i++) {
      const { input, label } = this.createSliderRow(
        camPosLabels[i], CAM_POS_MIN, CAM_POS_MAX, CAM_POS_STEP, camPosVals[i],
        (v) => {
          if (i === 0) this.camera.tx = Number(v.toFixed(3));
          if (i === 1) this.camera.ty = Number(v.toFixed(3));
          if (i === 2) this.camera.tz = Number(v.toFixed(3));
          this.onChanged();
        },
      );
      this.camInputs.push(input);
      this.camLabels.push(label);
      this.el.appendChild(input.parentElement!);
    }

    const camRotLabels = ['rX', 'rY', 'cYaw'] as const;
    const camRotVals = [this.camera.rxDeg, this.camera.ryDeg, this.camera.rzDeg] as const;
    for (let i = 0; i < 3; i++) {
      const { input, label } = this.createSliderRow(
        camRotLabels[i], DEG_MIN, DEG_MAX, CAM_ROT_STEP, camRotVals[i],
        (v) => {
          if (i === 0) this.camera.rxDeg = clampDeg(v);
          if (i === 1) this.camera.ryDeg = clampDeg(v);
          if (i === 2) this.camera.rzDeg = clampDeg(v);
          this.onChanged();
        },
      );
      this.camInputs.push(input);
      this.camLabels.push(label);
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
    (input as any).__row = row;
    Object.defineProperty(input, 'parentElement', {
      get() { return row; },
      configurable: true,
    });

    return { input, label: valSpan };
  }

  // ─── Logic ───────────────────────────────────

  private onChanged(): void {
    this.applyTransform();
    this.updateView();
  }

  private applyTransform(): void {
    this.armVisual.setRootDebugRotation(this.rotation[0], this.rotation[1], this.rotation[2]);
    this.armVisual.setRootDebugPosition(this.position[0], this.position[1], this.position[2]);
    this.bus.emit('debug:cameraTransform', {
      tx: this.camera.tx,
      ty: this.camera.ty,
      tz: this.camera.tz,
      rxDeg: this.camera.rxDeg,
      ryDeg: this.camera.ryDeg,
      rzDeg: this.camera.rzDeg,
    });
  }

  private updateView(): void {
    for (let i = 0; i < 3; i++) {
      if (this.rotInputs[i]) this.rotInputs[i].value = String(Math.round(this.rotation[i]));
      if (this.rotLabels[i]) this.rotLabels[i].textContent = `${Math.round(this.rotation[i])}°`;
      if (this.posInputs[i]) this.posInputs[i].value = String(this.position[i]);
      if (this.posLabels[i]) this.posLabels[i].textContent = this.position[i].toFixed(1);
    }
    const camVals = [
      this.camera.tx,
      this.camera.ty,
      this.camera.tz,
      this.camera.rxDeg,
      this.camera.ryDeg,
      this.camera.rzDeg,
    ];
    for (let i = 0; i < 6; i++) {
      if (this.camInputs[i]) this.camInputs[i].value = String(camVals[i]);
      if (this.camLabels[i]) {
        this.camLabels[i].textContent = i < 3 ? camVals[i].toFixed(3) : `${Math.round(camVals[i])}°`;
      }
    }
    if (this.packedEl) {
      const r = this.rotation.map((v) => Math.round(v));
      const p = this.position.map((v) => v.toFixed(1));
      this.packedEl.textContent = `R ${r.join('/')}  P ${p.join('/')}  Cxyz ${this.camera.tx.toFixed(3)}/${this.camera.ty.toFixed(3)}/${this.camera.tz.toFixed(3)}  Crot ${Math.round(this.camera.rxDeg)}/${Math.round(this.camera.ryDeg)}/${Math.round(this.camera.rzDeg)}(yaw@cZ)`;
    }
  }

  private reset(): void {
    this.rotation = [0, 0, 0];
    this.position = [0, 0, 0];
    this.camera = {
      tx: TRANSFORMS.flangeToCamera.translation.x,
      ty: TRANSFORMS.flangeToCamera.translation.y,
      tz: TRANSFORMS.flangeToCamera.translation.z,
      rxDeg: CAM_DEFAULT_RX_DEG,
      ryDeg: CAM_DEFAULT_RY_DEG,
      rzDeg: (TRANSFORMS.flangeToCamera.rotation.yaw * 180) / Math.PI,
    };
    this.onChanged();
  }

  dispose(): void {
    if (this.el?.parentNode) {
      this.el.parentNode.removeChild(this.el);
    }
    this.el = null;
    this.rotInputs = [];
    this.rotLabels = [];
    this.posInputs = [];
    this.posLabels = [];
    this.camInputs = [];
    this.camLabels = [];
    this.packedEl = null;
  }
}
