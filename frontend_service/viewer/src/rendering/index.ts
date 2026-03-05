/**
 * rendering/index.ts — Barrel re-export for rendering layer.
 */

export { SceneManager } from './scene-manager';
export { ArmVisual } from './arm-visual';
export { DepthCloudVisual } from './depth-cloud-visual';
export { DebugHelpers, createRosAxes, createCameraMount } from './debug-helpers';
export { AgvMapVisual } from './agv-map-visual';
export { WorkspaceVisual } from './workspace-visual';
export * from './coord-converter';
