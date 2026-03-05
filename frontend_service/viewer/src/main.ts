/**
 * main.ts — Application entry point.
 *
 * Wires all layers together:
 *   Data → EventBus → Domain → EventBus → Rendering → Canvas
 *
 * Lifecycle:
 *   1. Create EventBus
 *   2. Create SceneManager (renderer, scene, camera)
 *   3. Create ArmVisual + DepthCloudVisual (rendering layer)
 *   4. Create MessageBridge + DepthApi + RobotStatusPoller (data layer)
 *   5. Start animation loop
 *   6. Send 'arm3d:ready' to host
 */

import { createEventBus } from '@/event-bus';
import { Orchestrator } from '@/orchestrator';
import { SceneManager } from '@/rendering/scene-manager';
import { ArmVisual } from '@/rendering/arm-visual';
import { DepthCloudVisual } from '@/rendering/depth-cloud-visual';
import { DebugHelpers } from '@/rendering/debug-helpers';
import { AgvMapVisual } from '@/rendering/agv-map-visual';
import { WorkspaceVisual } from '@/rendering/workspace-visual';
import { MessageBridge } from '@/data/message-bridge';
import { DepthApi } from '@/data/depth-api';
import { RobotStatusPoller } from '@/data/robot-status';
import { CoordsHud } from '@/ui/coords-hud';
import { DebugPanel } from '@/ui/debug-panel';
import { CloudControls } from '@/ui/cloud-controls';
import { DepthCameraPanel } from '@/ui/depth-camera-panel';
import { decodeDMP1 } from '@/domain/depth-codec';
import { DEBUG_PARAMS, AGV_VISUALIZATION, LIFT, WORLD } from '@/config/scene-config';
import type { LiftConfig } from '@/domain/lift-model';
import { isTraceEnabled, trace } from '@/utils/trace';
import * as THREE from 'three';

function main(): void {
  // ─── Canvas ───────────────────────────────────
  const canvas = document.getElementById('arm3d-canvas') as HTMLCanvasElement | null;
  if (!canvas) {
    console.error('[arm3d] Canvas #arm3d-canvas not found');
    return;
  }

  // ─── Parse URL params ─────────────────────────
  const params = new URLSearchParams(window.location.search);
  const traceOn = isTraceEnabled();

  // Debug visualizations
  const showDepth = params.has(DEBUG_PARAMS.depthCloud)
    && params.get(DEBUG_PARAMS.depthCloud) !== '0';
  const showAxes = params.has(DEBUG_PARAMS.axes)
    ? (params.get(DEBUG_PARAMS.axes) === 'all' ? 'all' as const : true)
    : false;
  const showFrustum = params.has(DEBUG_PARAMS.cameraFrustum);
  const showCoords = params.has(DEBUG_PARAMS.coords);
  const showTransform = params.has(DEBUG_PARAMS.transform);
  const showWorkspace = params.has(DEBUG_PARAMS.workspace)
    && params.get(DEBUG_PARAMS.workspace) !== '0';

  // Polling
  const enableFallbackPolling = params.get('fallback') === '1'
    || (!params.has('noPolling') && !params.has('fallback'));

  // Arm configuration overrides
  const paramAxis = params.get('axis') ? (parseInt(params.get('axis')!, 10) || 6) : null;
  const paramType = params.get('type') ? (parseInt(params.get('type')!, 10) || 6) : null;
  const useStl = params.get('stl') !== '0';
  const ignoreMount = params.get('ignore_mount') === '1';

  // Debug transform overrides
  const debugRotation: [number, number, number] | null = showTransform ? [
    parseFloat(params.get('dbg_rx') ?? '0') || 0,
    parseFloat(params.get('dbg_ry') ?? '0') || 0,
    parseFloat(params.get('dbg_rz') ?? '0') || 0,
  ] : null;
  const debugPosition: [number, number, number] | null = showTransform ? [
    parseFloat(params.get('dbg_px') ?? '0') || 0,
    parseFloat(params.get('dbg_py') ?? '0') || 0,
    parseFloat(params.get('dbg_pz') ?? '0') || 0,
  ] : null;

  // ─── EventBus ─────────────────────────────────
  const bus = createEventBus();
  const depthApi = new DepthApi({ bus });
  const messageBridge = new MessageBridge({ bus });

  // ─── Domain orchestrator ──────────────────────
  const orchestrator = new Orchestrator({ bus });

  // ─── Rendering layer ──────────────────────────
  // Detect low-end device (Raspberry Pi or explicit param)
  const lowQuality = params.has('low_quality')
    || /arm|aarch64/i.test(navigator.userAgent)
    || (navigator.hardwareConcurrency !== undefined && navigator.hardwareConcurrency <= 4);

  const sceneManager = new SceneManager({ canvas, bus, lowQuality });
  const armVisual = new ArmVisual(sceneManager.scene, bus, { useStl });
  const depthCloud = new DepthCloudVisual(sceneManager.scene, bus);

  // Apply query-param overrides
  if (debugRotation) {
    armVisual.setRootDebugRotation(debugRotation[0], debugRotation[1], debugRotation[2]);
  }
  if (debugPosition) {
    armVisual.setRootDebugPosition(debugPosition[0], debugPosition[1], debugPosition[2]);
  }
  if (ignoreMount) {
    armVisual.setIgnoreMount(true);
  }

  // If axis/type overrides specified, trigger arm init immediately
  if (paramAxis !== null) {
    bus.emit('robot:status', {
      identity: {
        axis: paramAxis as 5 | 6 | 7,
        deviceType: paramType ?? paramAxis,
        endEffector: 'xarm_vacuum_gripper',
      },
    });
  }

  // Debug visualizations (axes, frustum)
  const debugHelpers = (showAxes || showFrustum)
    ? new DebugHelpers({
        scene: sceneManager.scene,
        bus,
        showAxes,
        showFrustum,
        getToolGroup: () => armVisual.getToolGroup(),
      })
    : null;

  // Workspace boundary visualization
  const workspaceVisual = showWorkspace
    ? new WorkspaceVisual({
        armRootGroup: armVisual.rootGroup,
        baseGroupOffset: new THREE.Vector3(0, -5, 0),  // groups[0] offset in rootGroup
      })
    : null;

  // AGV map layer
  const showAgvMap = params.has(DEBUG_PARAMS.agvMap)
    ? params.get(DEBUG_PARAMS.agvMap) !== '0'
    : AGV_VISUALIZATION.enabled;
  const agvMapVisual = showAgvMap
    ? new AgvMapVisual({
        scene: sceneManager.scene,
        bus,
        armRootGroup: armVisual.rootGroup,
        camera: sceneManager.camera,
        controls: sceneManager.controls,
      })
    : null;

  // ─── UI layer ─────────────────────────────────

  // Coords HUD: TCP position, joint angles, lift, AGV
  const liftCfg: LiftConfig = {
    worldUnitsPerTenK: LIFT.worldUnitsPerTenK,
    directionPerL_threeYup: LIFT.directionPerL_threeYup,
    group1BaseY_wu: LIFT.group1BaseY_wu,
    positionLimits: LIFT.positionLimits,
    scaleFactor: WORLD.SCALE_FACTOR,
  };
  const coordsHud = showCoords
    ? new CoordsHud({ bus, liftCfg })
    : null;

  // Debug transform panel: rotation/position sliders
  const debugPanel = showTransform
    ? new DebugPanel({
        armVisual,
        bus,
        initRotation: debugRotation as [number, number, number] | null,
        initPosition: debugPosition as [number, number, number] | null,
        onPointerDown: () => { sceneManager.controls.enabled = false; },
        onPointerUp: () => { sceneManager.controls.enabled = true; },
      })
    : null;

  // Cloud controls: record/pause, shot, save/load, clear
  // Shown when depth_cloud OR agv_map is active
  const showCloud = showDepth || showAgvMap;
  const cloudControls = showCloud
    ? new CloudControls({
        bus,
        getVoxels: () => orchestrator.getVoxels(),
        saveVoxelMap: (voxels) => depthApi.saveVoxelMap(voxels),
        loadVoxelMap: () => depthApi.loadVoxelMap(),
        deleteVoxelMap: () => depthApi.deleteVoxelMap(),
      })
    : null;

  const depthCameraPanel = showCloud
    ? new DepthCameraPanel(bus)
    : null;

  if (showCloud) {
    bus.emit('ui:toggleDepth', true);
  }

  // ─── Data layer ───────────────────────────────
  if (traceOn) {
    trace('main', 'viewer boot', {
      query: window.location.search,
      showDepth,
      enableFallbackPolling,
      showAgvMap,
      ignoreMount,
      buildTag: 'fallback-startup-gate-2026-02-26',
    });

    bus.on('arm:init', (p) => trace('bus', 'arm:init', p));
    bus.on('raw:joints', (p) => trace('bus', 'raw:joints', { count: p.angles.length, ts: p.timestamp }));
    bus.on('raw:lift', (p) => trace('bus', 'raw:lift', { motorUnits: p.motorUnits, ts: p.timestamp }));
    bus.on('arm:lift', (p) => trace('bus', 'arm:lift', { motorUnits: p.motorUnits, ts: p.timestamp }));
    bus.on('arm:mount', (p) => trace('bus', 'arm:mount', p));
    bus.on('agv:pose', (p) => trace('bus', 'agv:pose', p));
    bus.on('raw:depthFrame', (p) => trace('bus', 'raw:depthFrame', {
      width: p.width,
      height: p.height,
      depthLen: p.depthRaw.length,
      rgbLen: p.rgbRaw?.length ?? 0,
    }));
    bus.on('depth:cloud', (p) => trace('bus', 'depth:cloud', { count: p.count }));
    bus.on('transform:resolved', (p) => trace('bus', 'transform:resolved', {
      tcp: p.tcp.position,
      camera: p.camera.position,
      armBase: p.armBase.position,
    }));
    bus.on('error', (e) => trace('bus', `error:${e.source}`, { message: e.message, detail: e.detail }));
  }

  let statusPoller: RobotStatusPoller | null = null;
  if (enableFallbackPolling) {
    statusPoller = new RobotStatusPoller({
      bus,
      pollIntervalMs: 5000,
    });

    // In standalone mode, poll depth frames only when depth cloud is explicitly enabled.
    // This prevents background depth traffic for AGV-map-only sessions (depth_cloud=0).
    if (showDepth) {
      depthApi.startPolling(3000);
    }
  }

  // Disable fallback polling when host explicitly takes over via arm3d:config
  bus.on('arm:config', (cfg) => {
    if (cfg.fallbackPolling === false && statusPoller) {
      statusPoller.stop();
      depthApi.stopPolling();
      console.info('[arm3d] Host took over data — fallback polling disabled');
    }
  });

  // Auto-load persisted depth map on startup only when depth cloud is enabled.
  if (showDepth) {
    depthApi.loadDepthMap().then((buf) => {
      if (buf && buf.byteLength > 0) {
        try {
          const result = decodeDMP1(buf);
          // Use voxel:load to populate orchestrator's VoxelMap + rendering
          bus.emit('voxel:load', result.voxels);
          console.info(`[arm3d] Auto-loaded ${result.voxels.length} voxels from saved map`);
        } catch (e) {
          console.warn('[arm3d] Failed to decode persisted depth map:', e);
        }
      }
    }).catch(() => { /* silent */ });
  }

  // ─── Start ────────────────────────────────────
  const _tmpVec3 = new THREE.Vector3();
  sceneManager.onUpdate(() => {
    armVisual.update();
    // Update TCP coords in HUD from tool group world position
    if (coordsHud) {
      const tg = armVisual.getToolGroup();
      if (tg) {
        tg.getWorldPosition(_tmpVec3);
        coordsHud.updateTcp(_tmpVec3.x, _tmpVec3.y, _tmpVec3.z);
      }
    }
  });
  sceneManager.start();
  messageBridge.sendReady();

  bus.emit('viewer:ready', { version: 2 });

  // ─── Cleanup on unload ────────────────────────
  const cleanup = (): void => {
    bus.emit('viewer:dispose');
    depthApi.dispose();
    statusPoller?.dispose();
    messageBridge.dispose();
    cloudControls?.dispose();
    debugPanel?.dispose();
    depthCameraPanel?.dispose();
    coordsHud?.dispose();
    depthCloud.dispose();
    agvMapVisual?.dispose();
    workspaceVisual?.dispose();
    debugHelpers?.dispose();
    armVisual.dispose();
    sceneManager.dispose();
    orchestrator.dispose();
    bus.dispose();
  };

  window.addEventListener('beforeunload', cleanup);

  // Status text
  const statusEl = document.getElementById('arm3d-status');
  if (statusEl) statusEl.textContent = 'arm3d v2: ready';

  // Expose for debugging
  (window as any).__arm3d = { bus, orchestrator, sceneManager, armVisual, depthCloud, depthApi, debugHelpers, agvMapVisual, workspaceVisual, coordsHud, debugPanel, depthCameraPanel, cloudControls };
}

// ─── Bootstrap ──────────────────────────────────

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', main);
} else {
  main();
}
