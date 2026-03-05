import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { createEventBus } from '../event-bus';
import { RobotStatusPoller } from '../data/robot-status';

describe('RobotStatusPoller canon checks', () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it('emits unified robot:status with identity and raw:joints in fallback flow', async () => {
    const bus = createEventBus();
    const poller = new RobotStatusPoller({ bus });
    const statusSpy = vi.fn();

    bus.on('robot:status', statusSpy);

    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        xarm: {
          xarm_axis: 6,
          xarm_device_type: 6,
          data: Array.from({ length: 52 }, (_, idx) => (idx === 51 ? [30, -30] : null)),
          joints: [1, 2, 3, 4, 5, 6],
        },
        igus: { position: 1000 },
      }),
    } as Response);

    await (poller as any).fetchOnce();

    expect(statusSpy).toHaveBeenCalled();
    const emitted = statusSpy.mock.calls[0][0];
    expect(emitted.identity).toBeDefined();
    expect(emitted.rawJoints).toBeDefined();
    expect(emitted.rawJoints.angles).toEqual([1, 2, 3, 4, 5, 6]);

    poller.dispose();
  });

  it('deduplicates unchanged mount across polling ticks in robot:status', async () => {
    const bus = createEventBus();
    const poller = new RobotStatusPoller({ bus });
    const statusSpy = vi.fn();

    bus.on('robot:status', statusSpy);

    const payload = {
      xarm: {
        xarm_axis: 6,
        xarm_device_type: 6,
        data: Array.from({ length: 52 }, (_, idx) => (idx === 51 ? [30, -30] : null)),
        joints: [1, 2, 3, 4, 5, 6],
      },
      igus: { position: 1000 },
    };

    globalThis.fetch = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => payload } as Response)
      .mockResolvedValueOnce({ ok: true, json: async () => payload } as Response);

    await (poller as any).fetchOnce();
    await (poller as any).fetchOnce();

    const withMount = statusSpy.mock.calls
      .map((c) => c[0])
      .filter((s) => s.mount !== undefined);
    expect(withMount).toHaveLength(1);
    expect(withMount[0].mount).toEqual({ tilt: 30, rotation: -30 });

    poller.dispose();
  });

  it('gates joints/lift until mount is known', async () => {
    const bus = createEventBus();
    const poller = new RobotStatusPoller({ bus });
    const statusSpy = vi.fn();

    bus.on('robot:status', statusSpy);

    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        xarm: {
          xarm_axis: 6,
          xarm_device_type: 6,
          data: [],
          joints: [1, 2, 3, 4, 5, 6],
        },
        igus: { position: 1000 },
      }),
    } as Response);

    await (poller as any).fetchOnce();

    expect(statusSpy).toHaveBeenCalled();
    const emitted = statusSpy.mock.calls[0][0];
    expect(emitted.rawJoints).toBeUndefined();
    expect(emitted.rawLift).toBeUndefined();

    poller.dispose();
  });
});
