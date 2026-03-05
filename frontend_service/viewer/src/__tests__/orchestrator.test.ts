/**
 * orchestrator.test.ts — Integration tests for the Orchestrator.
 *
 * Tests the domain orchestration layer:
 *   - robot:status(rawJoints) → arm:joints + fk:updated + transform:resolved
 *   - robot:status(rawLift) → arm:lift + transform:resolved
 *   - raw:depthFrame → depth:frame + depth:cloud
 */
import { describe, it, expect, vi } from 'vitest';
import { createEventBus } from '../event-bus';
import { Orchestrator } from '../orchestrator';

describe('Orchestrator', () => {
  it('transforms robot:status(rawJoints) into arm:joints + fk:updated', () => {
    const bus = createEventBus();
    new Orchestrator({ bus });

    const jointsSpy = vi.fn();
    const fkSpy = vi.fn();
    bus.on('arm:joints', jointsSpy);
    bus.on('fk:updated', fkSpy);

    bus.emit('robot:status', {
      identity: { axis: 6, deviceType: 6, endEffector: 'xarm_vacuum_gripper' },
      rawJoints: { angles: [10, 20, 30, 40, 50, 60], timestamp: 1000 },
    });

    expect(jointsSpy).toHaveBeenCalledOnce();
    expect(jointsSpy.mock.calls[0][0].angles).toEqual([10, 20, 30, 40, 50, 60]);
    expect(jointsSpy.mock.calls[0][0].timestamp).toBe(1000);

    expect(fkSpy).toHaveBeenCalledOnce();
    expect(fkSpy.mock.calls[0][0].rotations).toBeDefined();
    expect(fkSpy.mock.calls[0][0].fkMatrix).toBeDefined();
  });

  it('transforms robot:status(rawLift) into arm:lift + transform:resolved', () => {
    const bus = createEventBus();
    new Orchestrator({ bus });

    const liftSpy = vi.fn();
    const chainSpy = vi.fn();
    bus.on('arm:lift', liftSpy);
    bus.on('transform:resolved', chainSpy);

    bus.emit('robot:status', { rawLift: { motorUnits: 50000, timestamp: 2000 } });

    expect(liftSpy).toHaveBeenCalledOnce();
    expect(liftSpy.mock.calls[0][0].motorUnits).toBe(50000);
    expect(chainSpy).toHaveBeenCalledOnce();
    expect(chainSpy.mock.calls[0][0].worldToCamera).toBeDefined();
  });

  it('transforms raw:depthFrame into depth:frame + depth:cloud', () => {
    const bus = createEventBus();
    new Orchestrator({ bus });

    const frameSpy = vi.fn();
    const cloudSpy = vi.fn();
    bus.on('depth:frame', frameSpy);
    bus.on('depth:cloud', cloudSpy);

    bus.emit('camera:worldMatrix', {
      elements: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
    });

    // Emit a small raw depth frame (4×4)
    const depthRaw = new Uint16Array(16).fill(500);
    bus.emit('raw:depthFrame', {
      depthRaw,
      rgbRaw: null,
      width: 4,
      height: 4,
      timestamp: 3000,
      timestampSource: 'source',
    });

    expect(frameSpy).toHaveBeenCalledOnce();
    expect(frameSpy.mock.calls[0][0].width).toBe(4);

    expect(cloudSpy).toHaveBeenCalledOnce();
    expect(cloudSpy.mock.calls[0][0].count).toBeGreaterThanOrEqual(0);
    expect(cloudSpy.mock.calls[0][0].positions).toBeInstanceOf(Float32Array);
  });

  it('computes FK for robot:status(joints)', () => {
    const bus = createEventBus();
    new Orchestrator({ bus });

    bus.emit('robot:status', {
      identity: { axis: 6, deviceType: 6, endEffector: 'xarm_vacuum_gripper' },
    });

    const fkSpy = vi.fn();
    bus.on('fk:updated', fkSpy);

    bus.emit('robot:status', {
      joints: { angles: [0, 0, 0, 0, 0, 0], timestamp: 4000 },
    });

    expect(fkSpy).toHaveBeenCalledOnce();
  });

  it('resolves chain on robot:status(agvPose)', () => {
    const bus = createEventBus();
    new Orchestrator({ bus });

    const chainSpy = vi.fn();
    bus.on('transform:resolved', chainSpy);

    bus.emit('robot:status', { agvPose: { x_m: 1.5, y_m: 2.0, theta_deg: 45, map_id: 1 } });

    expect(chainSpy).toHaveBeenCalledOnce();
    const resolved = chainSpy.mock.calls[0][0];
    expect(resolved.worldToCamera).toBeDefined();
    expect(resolved.tcp).toBeDefined();
  });

  it('robot:status composite update triggers FK + chain resolve', () => {
    const bus = createEventBus();
    new Orchestrator({ bus });

    const fkSpy = vi.fn();
    const chainSpy = vi.fn();
    bus.on('fk:updated', fkSpy);
    bus.on('transform:resolved', chainSpy);

    bus.emit('robot:status', {
      identity: { axis: 6, deviceType: 6, endEffector: 'xarm_vacuum_gripper' },
      joints: { angles: [10, 20, 30, 40, 50, 60], timestamp: 5000 },
      lift: { motorUnits: 10000, timestamp: 5000 },
      mount: { tilt: 0, rotation: 0 },
    });

    expect(fkSpy).toHaveBeenCalledOnce();
    expect(chainSpy).toHaveBeenCalled();
  });

  it('rejects shot accumulation when AGV map_id mismatches preferred map', () => {
    const bus = createEventBus();
    new Orchestrator({ bus });

    const dropSpy = vi.fn();
    const voxelSpy = vi.fn();
    bus.on('depth:dropped', dropSpy);
    bus.on('voxel:updated', voxelSpy);

    const now = Date.now();

    bus.emit('camera:worldMatrix', {
      elements: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
    });

    bus.emit('robot:status', {
      identity: { axis: 6, deviceType: 6, endEffector: 'xarm_vacuum_gripper' },
      rawJoints: { angles: [0, 0, 0, 0, 0, 0], timestamp: now },
      agvPose: { x_m: 0, y_m: 0, theta_deg: 0, map_id: 2 },
    });

    const depthRaw = new Uint16Array(16).fill(500);
    bus.emit('raw:depthFrame', {
      depthRaw,
      rgbRaw: null,
      width: 4,
      height: 4,
      timestamp: now,
      timestampSource: 'source',
    });

    bus.emit('ui:shot');

    expect(voxelSpy).not.toHaveBeenCalled();
    expect(dropSpy).toHaveBeenCalled();
    const reasons = dropSpy.mock.calls.map((c) => c[0]?.reason);
    expect(reasons).toContain('map_id_mismatch');
  });

  it('requests immediate depth fetch on ui:shotColor', () => {
    const bus = createEventBus();
    new Orchestrator({ bus });

    const fetchNowSpy = vi.fn();
    bus.on('depth:fetchNow', fetchNowSpy);

    bus.emit('ui:shotColor');

    expect(fetchNowSpy).toHaveBeenCalledOnce();
  });

  it('drops frame on hard skew only for source timestamps', () => {
    const bus = createEventBus();
    new Orchestrator({ bus });

    const cloudSpy = vi.fn();
    const dropSpy = vi.fn();
    bus.on('depth:cloud', cloudSpy);
    bus.on('depth:dropped', dropSpy);

    bus.emit('camera:worldMatrix', {
      elements: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
    });

    bus.emit('robot:status', {
      identity: { axis: 6, deviceType: 6, endEffector: 'xarm_vacuum_gripper' },
      rawJoints: { angles: [0, 0, 0, 0, 0, 0], timestamp: 1000 },
      agvPose: { x_m: 0, y_m: 0, theta_deg: 0, map_id: 1 },
    });

    bus.emit('raw:depthFrame', {
      depthRaw: new Uint16Array(16).fill(500),
      rgbRaw: null,
      width: 4,
      height: 4,
      timestamp: 10_000,
      timestampSource: 'source',
    });

    const reasons = dropSpy.mock.calls.map((c) => c[0]?.reason);
    expect(reasons).toContain('skew');
    expect(cloudSpy).not.toHaveBeenCalled();
  });

  it('does not hard-drop on skew for local fallback timestamps', () => {
    const bus = createEventBus();
    new Orchestrator({ bus });

    const cloudSpy = vi.fn();
    const dropSpy = vi.fn();
    bus.on('depth:cloud', cloudSpy);
    bus.on('depth:dropped', dropSpy);

    bus.emit('camera:worldMatrix', {
      elements: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
    });

    bus.emit('robot:status', {
      identity: { axis: 6, deviceType: 6, endEffector: 'xarm_vacuum_gripper' },
      rawJoints: { angles: [0, 0, 0, 0, 0, 0], timestamp: 1000 },
      agvPose: { x_m: 0, y_m: 0, theta_deg: 0, map_id: 1 },
    });

    bus.emit('raw:depthFrame', {
      depthRaw: new Uint16Array(16).fill(500),
      rgbRaw: null,
      width: 4,
      height: 4,
      timestamp: 10_000,
      timestampSource: 'local',
    });

    const reasons = dropSpy.mock.calls.map((c) => c[0]?.reason);
    expect(reasons).not.toContain('skew');
    expect(cloudSpy).toHaveBeenCalled();
  });

  it('times out pending shot color after 5s if no frame arrives', () => {
    vi.useFakeTimers();
    try {
      const bus = createEventBus();
      new Orchestrator({ bus });

      const dropSpy = vi.fn();
      bus.on('depth:dropped', dropSpy);

      bus.emit('ui:shotColor');
      vi.advanceTimersByTime(5000);

      expect(dropSpy).toHaveBeenCalled();
      const reasons = dropSpy.mock.calls.map((c) => c[0]?.reason);
      expect(reasons).toContain('stale');
      const timeoutCall = dropSpy.mock.calls.find((c) => String(c[0]?.detail ?? '').includes('timed out'));
      expect(timeoutCall).toBeDefined();
    } finally {
      vi.useRealTimers();
    }
  });
});
