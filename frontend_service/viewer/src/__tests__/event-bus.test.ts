/**
 * event-bus.test.ts — Unit tests for the typed event bus.
 */
import { describe, it, expect, vi } from 'vitest';
import { createEventBus } from '../event-bus';

describe('EventBus', () => {
  it('delivers payload to subscriber', () => {
    const bus = createEventBus();
    const handler = vi.fn();
    bus.on('viewer:ready', handler);
    bus.emit('viewer:ready', { version: 1 });
    expect(handler).toHaveBeenCalledWith({ version: 1 });
  });

  it('supports multiple subscribers', () => {
    const bus = createEventBus();
    const a = vi.fn();
    const b = vi.fn();
    bus.on('viewer:ready', a);
    bus.on('viewer:ready', b);
    bus.emit('viewer:ready', { version: 2 });
    expect(a).toHaveBeenCalledOnce();
    expect(b).toHaveBeenCalledOnce();
  });

  it('unsubscribe removes only that handler', () => {
    const bus = createEventBus();
    const a = vi.fn();
    const b = vi.fn();
    const unsub = bus.on('viewer:ready', a);
    bus.on('viewer:ready', b);
    unsub();
    bus.emit('viewer:ready', { version: 3 });
    expect(a).not.toHaveBeenCalled();
    expect(b).toHaveBeenCalledOnce();
  });

  it('once() fires handler only once', () => {
    const bus = createEventBus();
    const handler = vi.fn();
    bus.once('viewer:resize', handler);
    bus.emit('viewer:resize', { width: 800, height: 600 });
    bus.emit('viewer:resize', { width: 1024, height: 768 });
    expect(handler).toHaveBeenCalledOnce();
    expect(handler).toHaveBeenCalledWith({ width: 800, height: 600 });
  });

  it('off() removes all listeners for that event', () => {
    const bus = createEventBus();
    const a = vi.fn();
    const b = vi.fn();
    bus.on('voxel:clear', a);
    bus.on('voxel:clear', b);
    bus.off('voxel:clear');
    bus.emit('voxel:clear', undefined as unknown as void);
    expect(a).not.toHaveBeenCalled();
    expect(b).not.toHaveBeenCalled();
  });

  it('dispose() removes everything', () => {
    const bus = createEventBus();
    const a = vi.fn();
    const b = vi.fn();
    bus.on('viewer:ready', a);
    bus.on('depth:cloud', b);
    bus.dispose();
    bus.emit('viewer:ready', { version: 1 });
    bus.emit('depth:cloud', { positions: new Float32Array(0), colors: new Float32Array(0), count: 0 });
    expect(a).not.toHaveBeenCalled();
    expect(b).not.toHaveBeenCalled();
  });

  it('listenerCount is accurate', () => {
    const bus = createEventBus();
    expect(bus.listenerCount('viewer:ready')).toBe(0);
    const unsub1 = bus.on('viewer:ready', () => {});
    const unsub2 = bus.on('viewer:ready', () => {});
    expect(bus.listenerCount('viewer:ready')).toBe(2);
    unsub1();
    expect(bus.listenerCount('viewer:ready')).toBe(1);
    unsub2();
    expect(bus.listenerCount('viewer:ready')).toBe(0);
  });

  it('handler errors do not stop other handlers', () => {
    const bus = createEventBus();
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const a = vi.fn(() => { throw new Error('boom'); });
    const b = vi.fn();
    bus.on('viewer:ready', a);
    bus.on('viewer:ready', b);
    bus.emit('viewer:ready', { version: 1 });
    expect(a).toHaveBeenCalledOnce();
    expect(b).toHaveBeenCalledOnce();
    expect(errorSpy).toHaveBeenCalled();
    errorSpy.mockRestore();
  });

  it('emitting event with no listeners is a no-op', () => {
    const bus = createEventBus();
    expect(() => bus.emit('viewer:ready', { version: 1 })).not.toThrow();
  });
});
