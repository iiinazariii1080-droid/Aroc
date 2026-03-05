/**
 * depth-camera-panel.ts — Manual runtime tuning panel for depth camera params.
 *
 * UI layer only. Emits `depth:cameraParams` updates so orchestrator can
 * immediately apply intrinsics/calibration/depth scale changes to cloud build.
 */

import type { EventBus } from '@/event-bus';
import { DEPTH_CAMERA, DEPTH_OVERLAY } from '@/config/scene-config';
import type { DepthCameraRuntimeParams } from '@/types/depth';

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
  'line-height:1.5',
  'z-index:15',
  'min-width:260px',
].join(';');

const ROW_CSS = 'display:flex;align-items:center;gap:6px;margin:3px 0';
const LABEL_CSS = 'width:72px;color:#94a3b8';
const INPUT_CSS = 'flex:1;min-width:0;background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:4px;padding:2px 4px;font-size:11px';
const BTN_CSS = 'margin-top:6px;padding:3px 10px;border:1px solid #555;border-radius:4px;cursor:pointer;font-size:11px;background:#1a1a2e;color:#e2e8f0';

function num(value: string, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function defaultFarM(): number {
  return Math.max(
    DEPTH_CAMERA.frustum.far,
    ((DEPTH_OVERLAY.maxRaw * DEPTH_CAMERA.depthCalibration.k + DEPTH_CAMERA.depthCalibration.b)
      * DEPTH_CAMERA.depthScale) / 1000 + 0.25,
  );
}

export class DepthCameraPanel {
  private readonly bus: EventBus;
  private el: HTMLDivElement | null = null;
  private statusEl: HTMLSpanElement | null = null;
  private readonly inputs = new Map<string, HTMLInputElement>();

  constructor(bus: EventBus) {
    this.bus = bus;
    this.createPanel();
    this.applyDefaults();
  }

  private createPanel(): void {
    this.el = document.createElement('div');
    this.el.id = 'arm3d-depth-camera-panel';
    this.el.style.cssText = PANEL_CSS;

    const title = document.createElement('div');
    title.textContent = '📷 Depth Camera';
    title.style.fontWeight = 'bold';
    title.style.marginBottom = '6px';
    this.el.appendChild(title);

    this.el.appendChild(this.makeInput('fx', DEPTH_CAMERA.intrinsics.fx));
    this.el.appendChild(this.makeInput('fy', DEPTH_CAMERA.intrinsics.fy));
    this.el.appendChild(this.makeInput('cx', DEPTH_CAMERA.intrinsics.cx));
    this.el.appendChild(this.makeInput('cy', DEPTH_CAMERA.intrinsics.cy));
    this.el.appendChild(this.makeInput('depthScale', DEPTH_CAMERA.depthScale));
    this.el.appendChild(this.makeInput('k', DEPTH_CAMERA.depthCalibration.k));
    this.el.appendChild(this.makeInput('b', DEPTH_CAMERA.depthCalibration.b));
    this.el.appendChild(this.makeInput('near', DEPTH_CAMERA.frustum.near));
    this.el.appendChild(this.makeInput('far', defaultFarM()));

    const applyBtn = document.createElement('button');
    applyBtn.textContent = 'Apply';
    applyBtn.style.cssText = BTN_CSS;
    applyBtn.addEventListener('click', () => this.emitConfig());
    this.el.appendChild(applyBtn);

    const resetBtn = document.createElement('button');
    resetBtn.textContent = 'Reset';
    resetBtn.style.cssText = `${BTN_CSS};margin-left:6px`;
    resetBtn.addEventListener('click', () => this.applyDefaults());
    this.el.appendChild(resetBtn);

    this.statusEl = document.createElement('span');
    this.statusEl.style.cssText = 'display:block;margin-top:6px;color:#93c5fd';
    this.statusEl.textContent = 'ready';
    this.el.appendChild(this.statusEl);

    document.body.appendChild(this.el);
  }

  private makeInput(label: string, initial: number): HTMLDivElement {
    const row = document.createElement('div');
    row.style.cssText = ROW_CSS;

    const lbl = document.createElement('span');
    lbl.style.cssText = LABEL_CSS;
    lbl.textContent = label;
    row.appendChild(lbl);

    const input = document.createElement('input');
    input.type = 'number';
    input.step = 'any';
    input.value = String(initial);
    input.style.cssText = INPUT_CSS;
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') this.emitConfig();
    });
    row.appendChild(input);

    this.inputs.set(label, input);
    return row;
  }

  private applyDefaults(): void {
    this.setInput('fx', DEPTH_CAMERA.intrinsics.fx);
    this.setInput('fy', DEPTH_CAMERA.intrinsics.fy);
    this.setInput('cx', DEPTH_CAMERA.intrinsics.cx);
    this.setInput('cy', DEPTH_CAMERA.intrinsics.cy);
    this.setInput('depthScale', DEPTH_CAMERA.depthScale);
    this.setInput('k', DEPTH_CAMERA.depthCalibration.k);
    this.setInput('b', DEPTH_CAMERA.depthCalibration.b);
    this.setInput('near', DEPTH_CAMERA.frustum.near);
    this.setInput('far', defaultFarM());
    this.emitConfig();
  }

  private setInput(key: string, value: number): void {
    const input = this.inputs.get(key);
    if (input) input.value = String(value);
  }

  private emitConfig(): void {
    const payload: DepthCameraRuntimeParams = {
      intrinsics: {
        fx: num(this.inputs.get('fx')?.value ?? '', DEPTH_CAMERA.intrinsics.fx),
        fy: num(this.inputs.get('fy')?.value ?? '', DEPTH_CAMERA.intrinsics.fy),
        cx: num(this.inputs.get('cx')?.value ?? '', DEPTH_CAMERA.intrinsics.cx),
        cy: num(this.inputs.get('cy')?.value ?? '', DEPTH_CAMERA.intrinsics.cy),
      },
      depthScale: num(this.inputs.get('depthScale')?.value ?? '', DEPTH_CAMERA.depthScale),
      depthCalibration: {
        k: num(this.inputs.get('k')?.value ?? '', DEPTH_CAMERA.depthCalibration.k),
        b: num(this.inputs.get('b')?.value ?? '', DEPTH_CAMERA.depthCalibration.b),
      },
      frustum: {
        near: num(this.inputs.get('near')?.value ?? '', DEPTH_CAMERA.frustum.near),
        far: num(this.inputs.get('far')?.value ?? '', defaultFarM()),
      },
    };

    this.bus.emit('depth:cameraParams', payload);
    if (this.statusEl) this.statusEl.textContent = 'applied';
  }

  dispose(): void {
    if (this.el?.parentNode) {
      this.el.parentNode.removeChild(this.el);
    }
    this.el = null;
    this.statusEl = null;
    this.inputs.clear();
  }
}
