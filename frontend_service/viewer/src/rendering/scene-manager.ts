/**
 * scene-manager.ts — Three.js scene lifecycle.
 *
 * Rendering layer. Owns the renderer, scene, camera, controls, and animation loop.
 * Wired to the EventBus for resize/dispose events.
 */

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { SCENE_CAMERA, LIGHTS, FLOOR, SCENE } from '@/config/scene-config';
import type { EventBus } from '@/event-bus';

export interface SceneManagerOptions {
  canvas: HTMLCanvasElement;
  bus: EventBus;
}

export class SceneManager {
  readonly renderer: THREE.WebGLRenderer;
  readonly scene: THREE.Scene;
  readonly camera: THREE.PerspectiveCamera;
  readonly controls: OrbitControls;

  private animationId: number | null = null;
  private readonly bus: EventBus;
  private disposed = false;

  constructor(opts: SceneManagerOptions) {
    this.bus = opts.bus;

    // Renderer
    this.renderer = new THREE.WebGLRenderer({
      canvas: opts.canvas,
      antialias: SCENE.renderer.antialias,
      alpha: SCENE.renderer.alpha,
    });
    this.renderer.shadowMap.enabled = SCENE.renderer.shadowMapEnabled;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, SCENE.renderer.pixelRatioMax));
    this.renderer.setClearColor(SCENE.background);

    // Scene
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(SCENE.background);

    // Camera
    const aspect = opts.canvas.clientWidth / opts.canvas.clientHeight || 1;
    this.camera = new THREE.PerspectiveCamera(
      SCENE_CAMERA.fov, aspect, SCENE_CAMERA.near, SCENE_CAMERA.far,
    );
    const [ipx, ipy, ipz] = SCENE_CAMERA.initialPosition;
    this.camera.position.set(ipx, ipy, ipz);

    // Controls
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    const [itx, ity, itz] = SCENE_CAMERA.initialTarget;
    this.controls.target.set(itx, ity, itz);
    this.controls.enableDamping = SCENE_CAMERA.controls.enableDamping;
    this.controls.dampingFactor = SCENE_CAMERA.controls.dampingFactor;
    this.controls.minDistance = SCENE_CAMERA.controls.minDistance;
    this.controls.maxDistance = SCENE_CAMERA.controls.maxDistance;
    this.controls.update();

    // Lights
    this.setupLights();

    // Floor grid
    this.setupFloor();

    // Resize handling
    this.handleResize();
    window.addEventListener('resize', this.handleResize);

    // EventBus
    this.bus.on('viewer:dispose', () => this.dispose());
  }

  // ─── Lights ───────────────────────────────────

  private setupLights(): void {
    for (const lightCfg of LIGHTS) {
      if (lightCfg.type === 'ambient') {
        this.scene.add(new THREE.AmbientLight(lightCfg.color, lightCfg.intensity));
      } else if (lightCfg.type === 'directional') {
        const dl = new THREE.DirectionalLight(lightCfg.color, lightCfg.intensity);
        const [lx, ly, lz] = lightCfg.position;
        dl.position.set(lx, ly, lz);
        dl.castShadow = lightCfg.castShadow;
        if (dl.castShadow) {
          dl.shadow.mapSize.set(1024, 1024);
          dl.shadow.camera.near = 1;
          dl.shadow.camera.far = 100;
          const s = 30;
          dl.shadow.camera.left = -s;
          dl.shadow.camera.right = s;
          dl.shadow.camera.top = s;
          dl.shadow.camera.bottom = -s;
        }
        this.scene.add(dl);
      }
    }
  }

  // ─── Floor ────────────────────────────────────

  private setupFloor(): void {
    const grid = new THREE.GridHelper(
      FLOOR.gridSize, FLOOR.gridDivisions,
      FLOOR.gridColors[0], FLOOR.gridColors[1],
    );
    grid.position.y = FLOOR.positionY;
    this.scene.add(grid);
  }

  // ─── Resize ───────────────────────────────────

  private handleResize = (): void => {
    const canvas = this.renderer.domElement;
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    if (w === 0 || h === 0) return;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.bus.emit('viewer:resize', { width: w, height: h });
  };

  // ─── Animation loop ───────────────────────────

  start(): void {
    if (this.animationId !== null) return;
    const loop = () => {
      if (this.disposed) return;
      this.animationId = requestAnimationFrame(loop);
      this.controls.update();
      this.renderer.render(this.scene, this.camera);
    };
    loop();
  }

  stop(): void {
    if (this.animationId !== null) {
      cancelAnimationFrame(this.animationId);
      this.animationId = null;
    }
  }

  // ─── Dispose ──────────────────────────────────

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.stop();
    window.removeEventListener('resize', this.handleResize);
    this.controls.dispose();
    this.renderer.dispose();

    // Traverse and dispose geometries/materials
    this.scene.traverse((obj) => {
      if (obj instanceof THREE.Mesh) {
        obj.geometry?.dispose();
        if (Array.isArray(obj.material)) {
          obj.material.forEach(m => m.dispose());
        } else {
          obj.material?.dispose();
        }
      }
    });
  }
}
