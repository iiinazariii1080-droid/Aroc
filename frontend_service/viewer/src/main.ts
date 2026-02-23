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
import { SceneManager } from '@/rendering/scene-manager';
import { ArmVisual } from '@/rendering/arm-visual';
import { DepthCloudVisual } from '@/rendering/depth-cloud-visual';
import { MessageBridge } from '@/data/message-bridge';
import { DepthApi } from '@/data/depth-api';
import { RobotStatusPoller } from '@/data/robot-status';
import { DEBUG_PARAMS } from '@/config/scene-config';

function main(): void {
  // ─── Canvas ───────────────────────────────────
  const canvas = document.getElementById('arm3d-canvas') as HTMLCanvasElement | null;
  if (!canvas) {
    console.error('[arm3d] Canvas #arm3d-canvas not found');
    return;
  }

  // ─── Parse URL params ─────────────────────────
  const params = new URLSearchParams(window.location.search);
  const showDepth = params.has(DEBUG_PARAMS.depthCloud);
  const enableFallbackPolling = !params.has('noPolling');

  // ─── EventBus ─────────────────────────────────
  const bus = createEventBus();

  // ─── Rendering layer ──────────────────────────
  const sceneManager = new SceneManager({ canvas, bus });
  const armVisual = new ArmVisual(sceneManager.scene, bus);
  const depthCloud = new DepthCloudVisual(sceneManager.scene, bus);

  if (showDepth) {
    bus.emit('ui:toggleDepth', true);
  }

  // ─── Data layer ───────────────────────────────
  const messageBridge = new MessageBridge({ bus });
  const depthApi = new DepthApi({ bus });

  let statusPoller: RobotStatusPoller | null = null;
  if (enableFallbackPolling) {
    statusPoller = new RobotStatusPoller({
      bus,
      axisCount: 6,
      pollIntervalMs: 5000,
    });
  }

  // Disable fallback polling once host sends state
  bus.on('arm:joints', () => {
    if (statusPoller) {
      statusPoller.stop();
    }
  });

  // Update poller axis count when arm is initialized
  bus.on('arm:init', (identity) => {
    if (statusPoller) {
      statusPoller.setAxisCount(identity.axis);
    }
  });

  // ─── Start ────────────────────────────────────
  sceneManager.start();
  messageBridge.sendReady();

  bus.emit('viewer:ready', { version: 2 });

  // ─── Cleanup on unload ────────────────────────
  const cleanup = (): void => {
    bus.emit('viewer:dispose', undefined as unknown as void);
    depthApi.dispose();
    statusPoller?.dispose();
    messageBridge.dispose();
    depthCloud.dispose();
    armVisual.dispose();
    sceneManager.dispose();
    bus.dispose();
  };

  window.addEventListener('beforeunload', cleanup);

  // Status text
  const statusEl = document.getElementById('arm3d-status');
  if (statusEl) statusEl.textContent = 'arm3d v2: ready';

  // Expose for debugging
  (window as any).__arm3d = { bus, sceneManager, armVisual, depthCloud, depthApi };
}

// ─── Bootstrap ──────────────────────────────────

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', main);
} else {
  main();
}
