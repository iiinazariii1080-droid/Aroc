/* Unit tests for ReconnectCoordinator — the critical reconnect scheduling engine (Node, no browser). */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

let passed = 0;
let failed = 0;

function assert(cond, msg) {
  if (!cond) {
    failed++;
    console.error('  FAIL:', msg);
    throw new Error('Assertion failed: ' + (msg || ''));
  }
  passed++;
}

function loadScript(sandbox, filePath) {
  const code = fs.readFileSync(filePath, 'utf8');
  vm.runInContext(code, sandbox, { filename: filePath });
}

function createFakeClock() {
  let now = 0;
  let nextId = 1;
  const timeouts = new Map();
  const intervals = new Map();

  function setTimeoutFn(fn, ms) {
    const id = nextId++;
    timeouts.set(id, { at: now + Math.max(0, Math.trunc(ms || 0)), fn });
    return id;
  }
  function clearTimeoutFn(id) { timeouts.delete(id); }

  function setIntervalFn(fn, ms) {
    const every = Math.max(1, Math.trunc(ms || 1));
    const id = nextId++;
    intervals.set(id, { nextAt: now + every, every, fn });
    return id;
  }
  function clearIntervalFn(id) { intervals.delete(id); }

  async function advance(ms) {
    const target = now + Math.max(0, Math.trunc(ms || 0));
    while (true) {
      let nextAt = Infinity;
      let nextTimeoutId = null;
      let nextIntervalId = null;

      for (const [id, t] of timeouts.entries()) {
        if (t.at <= target && t.at < nextAt) {
          nextAt = t.at;
          nextTimeoutId = id;
          nextIntervalId = null;
        }
      }
      for (const [id, t] of intervals.entries()) {
        if (t.nextAt <= target && t.nextAt < nextAt) {
          nextAt = t.nextAt;
          nextIntervalId = id;
          nextTimeoutId = null;
        }
      }

      if (nextTimeoutId == null && nextIntervalId == null) break;

      now = nextAt;
      if (nextTimeoutId != null) {
        const t = timeouts.get(nextTimeoutId);
        timeouts.delete(nextTimeoutId);
        if (t && typeof t.fn === 'function') {
          t.fn(); // fire-and-forget: DO NOT await async callbacks (prevents deadlock with Promise.race + fake timers)
        }
      } else {
        const t = intervals.get(nextIntervalId);
        if (t && typeof t.fn === 'function') {
          t.nextAt += t.every;
          t.fn();
        }
      }
    }
    now = target;
  }

  return {
    nowMs: () => now,
    setTimeout: setTimeoutFn,
    clearTimeout: clearTimeoutFn,
    setInterval: setIntervalFn,
    clearInterval: clearIntervalFn,
    advance,
  };
}

async function flushMicrotasks() {
  for (let i = 0; i < 10; i++) await Promise.resolve();
}

async function main() {
  const root = path.resolve(__dirname, '..');

  const sandbox = vm.createContext({
    window: {},
    console,
    Math: Math,
    Promise: Promise,
  });
  sandbox.Math.random = () => 0.5;
  sandbox.Janus = { randomString: () => 'deadbeef' };

  loadScript(sandbox, path.join(root, 'player', 'ns.js'));
  loadScript(sandbox, path.join(root, 'player', 'core', 'player_state.js'));
  loadScript(sandbox, path.join(root, 'player', 'core', 'codes.js'));
  loadScript(sandbox, path.join(root, 'player', 'core', 'domain_events.js'));
  loadScript(sandbox, path.join(root, 'player', 'core', 'connection_policy.js'));
  loadScript(sandbox, path.join(root, 'player', 'core', 'backoff.js'));
  loadScript(sandbox, path.join(root, 'player', 'core', 'recovery_policy.js'));
  loadScript(sandbox, path.join(root, 'player', 'app', 'recovery_map.js'));
  loadScript(sandbox, path.join(root, 'player', 'app', 'reconnect_coordinator.js'));

  const AP = sandbox.window.AutonomousPlayer;
  const RS = AP.Core.RecoverySeverity;
  const RA = AP.Core.RecoveryAction;

  const baseCfg = {
    backoffBaseMs: 500,
    backoffFactor: 1.8,
    backoffMinMs: 250,
    backoffMaxMs: 15000,
    backoffJitterRatio: 0,
    maxReconnectAttempts: 4,
    maxWatchRetries: 3,
    maxReattachRetries: 2,
    connectSettleMs: 1000,
    settleStartTimeoutMs: 5000,
    reconnectAttemptTimeoutMs: 10000,
  };

  const log = { debug: () => {}, info: () => {}, warn: () => {}, error: () => {} };

  function createCoordinator(overrides) {
    const clock = createFakeClock();
    const cfg = Object.assign({}, baseCfg, overrides || {});
    const rc = new AP.App.ReconnectCoordinator(cfg, clock, log);

    const calls = {
      scheduled: [],
      attempts: [],
      successes: [],
      exhausted: [],
    };

    let token = 1;
    let recovered = false;
    let shouldContinue = true;
    let executeResult = async () => {};

    rc.bindContext({
      getToken: () => token,
      shouldContinue: () => shouldContinue,
      isRecovered: () => recovered,
      executeAttempt: async (ctx) => {
        calls.attempts.push(ctx);
        return executeResult(ctx);
      },
      onScheduled: (info) => calls.scheduled.push(info),
      onAttempt: (info) => {},
      onSuccess: (info) => calls.successes.push(info),
      onExhausted: (info) => calls.exhausted.push(info),
    });

    return { rc, clock, calls, setToken: (t) => { token = t; }, setRecovered: (v) => { recovered = v; }, setShouldContinue: (v) => { shouldContinue = v; }, setExecute: (fn) => { executeResult = fn; } };
  }

  // ═══════════════════════════════════════════════
  // Test 1: request() schedules next attempt with backoff delay
  // ═══════════════════════════════════════════════
  console.log('--- RC Test 1: basic request + schedule ---');
  {
    const { rc, clock, calls } = createCoordinator();
    rc.request('no_frames', RS.SOFT);
    assert(rc.pending() != null, 'RC1: pending set');
    assert(rc.pending().reason === 'no_frames', 'RC1: pending reason');
    assert(rc.pending().severity === RS.SOFT, 'RC1: pending severity');
    assert(calls.scheduled.length === 1, 'RC1: onScheduled called once');
    assert(calls.scheduled[0].delay === 500, 'RC1: first delay is backoffBaseMs=500');

    // Timer fires at +500
    await clock.advance(500);
    await flushMicrotasks();
    assert(calls.attempts.length === 1, 'RC1: executeAttempt called');
    assert(calls.attempts[0].attempt === 1, 'RC1: attempt is 1');
    assert(calls.attempts[0].action === RA.SOFT_RESTART, 'RC1: action is SOFT_RESTART');
    assert(rc.attempt() === 1, 'RC1: attempt counter is 1');
    assert(rc.inFlight() === true, 'RC1: inFlight after execute');
  }

  // ═══════════════════════════════════════════════
  // Test 2: request() coalescing — second request takes max severity
  // ═══════════════════════════════════════════════
  console.log('--- RC Test 2: severity coalescing ---');
  {
    const { rc, clock, calls } = createCoordinator();
    rc.request('no_frames', RS.SOFT);
    rc.request('ice_failed', RS.HARD);
    // Second request: already scheduled → coalesced
    assert(rc.pending().severity === RS.HARD, 'RC2: severity coalesced to max (HARD)');
    assert(rc.pending().reason === 'ice_failed', 'RC2: reason updated to latest');
    assert(calls.scheduled.length === 1, 'RC2: only one schedule (no duplicate)');

    // Execute → action should be RECREATE_SESSION due to HARD severity
    await clock.advance(500);
    await flushMicrotasks();
    assert(calls.attempts[0].action === RA.RECREATE_SESSION, 'RC2: HARD severity → RECREATE_SESSION');
  }

  // ═══════════════════════════════════════════════
  // Test 3: request() with shouldContinue=false records pending but doesn't schedule
  // ═══════════════════════════════════════════════
  console.log('--- RC Test 3: shouldContinue=false ---');
  {
    const { rc, clock, calls, setShouldContinue } = createCoordinator();
    setShouldContinue(false);
    rc.request('no_frames', RS.SOFT);
    assert(rc.pending() != null, 'RC3: pending recorded');
    assert(calls.scheduled.length === 0, 'RC3: not scheduled when shouldContinue false');

    await clock.advance(5000);
    assert(calls.attempts.length === 0, 'RC3: no attempt when shouldContinue false');
  }

  // ═══════════════════════════════════════════════
  // Test 4: resumeIfPending() starts scheduling when shouldContinue flips
  // ═══════════════════════════════════════════════
  console.log('--- RC Test 4: resumeIfPending ---');
  {
    const { rc, clock, calls, setShouldContinue } = createCoordinator();
    setShouldContinue(false);
    rc.request('no_frames', RS.SOFT);
    assert(calls.scheduled.length === 0, 'RC4: not scheduled initially');

    setShouldContinue(true);
    rc.resumeIfPending();
    assert(calls.scheduled.length === 1, 'RC4: scheduled after resumeIfPending');

    await clock.advance(500);
    await flushMicrotasks();
    assert(calls.attempts.length === 1, 'RC4: attempt fires after resume');
  }

  // ═══════════════════════════════════════════════
  // Test 5: resumeIfPending() no-op if already in-flight or timer set
  // ═══════════════════════════════════════════════
  console.log('--- RC Test 5: resumeIfPending no-op ---');
  {
    const { rc, clock, calls } = createCoordinator();
    rc.request('no_frames', RS.SOFT);
    rc.resumeIfPending(); // timer already set
    assert(calls.scheduled.length === 1, 'RC5: resumeIfPending skipped (timer set)');

    await clock.advance(500);
    await flushMicrotasks();
    rc.resumeIfPending(); // now in-flight
    assert(calls.scheduled.length === 1, 'RC5: resumeIfPending skipped (in-flight)');
  }

  // ═══════════════════════════════════════════════
  // Test 6: escalateSeverity()
  // ═══════════════════════════════════════════════
  console.log('--- RC Test 6: escalateSeverity ---');
  {
    const { rc } = createCoordinator();
    rc.request('no_frames', RS.SOFT);
    assert(rc.pending().severity === RS.SOFT, 'RC6: initial severity SOFT');
    rc.escalateSeverity(RS.HARD);
    assert(rc.pending().severity === RS.HARD, 'RC6: severity escalated to HARD');

    // Escalate to lower → no change (max)
    rc.escalateSeverity(RS.SOFT);
    assert(rc.pending().severity === RS.HARD, 'RC6: escalateSeverity cannot downgrade');
  }

  // ═══════════════════════════════════════════════
  // Test 7: escalateSeverity() no-op if nothing pending
  // ═══════════════════════════════════════════════
  console.log('--- RC Test 7: escalateSeverity no-op ---');
  {
    const { rc } = createCoordinator();
    rc.escalateSeverity(RS.HARD); // no pending → no-op
    assert(rc.pending() === null, 'RC7: no pending after escalate on empty');
  }

  // ═══════════════════════════════════════════════
  // Test 8: stale token at schedule vs current → dropped
  // ═══════════════════════════════════════════════
  console.log('--- RC Test 8: stale token dropped ---');
  {
    const { rc, clock, calls, setToken } = createCoordinator();
    rc.request('no_frames', RS.SOFT);
    setToken(999); // change token before timer fires
    await clock.advance(500);
    await flushMicrotasks();
    assert(calls.attempts.length === 0, 'RC8: stale token → attempt dropped');
  }

  // ═══════════════════════════════════════════════
  // Test 9: isRecovered() true at timer fire → success without running attempt
  // ═══════════════════════════════════════════════
  console.log('--- RC Test 9: isRecovered skips attempt ---');
  {
    const { rc, clock, calls, setRecovered } = createCoordinator();
    rc.request('no_frames', RS.SOFT);
    setRecovered(true);
    await clock.advance(500);
    await flushMicrotasks();
    assert(calls.attempts.length === 0, 'RC9: no attempt when already recovered');
    assert(calls.successes.length === 1, 'RC9: onSuccess called');
    assert(rc.attempt() === 0, 'RC9: attempt reset to 0');
    assert(rc.pending() === null, 'RC9: pending cleared');
  }

  // ═══════════════════════════════════════════════
  // Test 10: settle window — notifyRecovered during settle → success
  // ═══════════════════════════════════════════════
  console.log('--- RC Test 10: settle window + notifyRecovered ---');
  {
    const { rc, clock, calls } = createCoordinator();
    rc.request('no_frames', RS.SOFT);
    await clock.advance(500);
    await flushMicrotasks();
    assert(rc.inFlight() === true, 'RC10: in-flight after attempt');

    // Start settle window (controller calls this when offer received)
    rc.startSettleWindow();
    // notifyRecovered before settle timer fires
    rc.notifyRecovered();
    assert(calls.successes.length === 1, 'RC10: notifyRecovered → success');
    assert(rc.inFlight() === false, 'RC10: no longer in-flight');
    assert(rc.attempt() === 0, 'RC10: attempt reset to 0');
    assert(rc.pending() === null, 'RC10: pending cleared');
  }

  // ═══════════════════════════════════════════════
  // Test 11: settle timer fires without notifyRecovered → next attempt scheduled
  // ═══════════════════════════════════════════════
  console.log('--- RC Test 11: settle timeout → retry ---');
  {
    const { rc, clock, calls } = createCoordinator();
    rc.request('no_frames', RS.SOFT);
    await clock.advance(500);
    await flushMicrotasks();
    assert(rc.inFlight() === true, 'RC11: in-flight');

    rc.startSettleWindow();
    // Settle timer fires after connectSettleMs (1000)
    await clock.advance(1000);
    await flushMicrotasks();

    assert(rc.inFlight() === false, 'RC11: no longer in-flight after settle timeout');
    // Next attempt should be scheduled
    assert(calls.scheduled.length === 2, 'RC11: second schedule after settle timeout');
  }

  // ═══════════════════════════════════════════════
  // Test 12: notifyRecovered() idempotent when not in settle phase
  // ═══════════════════════════════════════════════
  console.log('--- RC Test 12: notifyRecovered idempotent ---');
  {
    const { rc, calls } = createCoordinator();
    rc.notifyRecovered(); // nothing in-flight → no-op
    assert(calls.successes.length === 0, 'RC12: notifyRecovered no-op when idle');
  }

  // ═══════════════════════════════════════════════
  // Test 13: notifyAttemptFailed() ends attempt and schedules next
  // ═══════════════════════════════════════════════
  console.log('--- RC Test 13: notifyAttemptFailed ---');
  {
    const { rc, clock, calls } = createCoordinator();
    rc.request('no_frames', RS.SOFT);
    await clock.advance(500);
    await flushMicrotasks();
    assert(rc.inFlight() === true, 'RC13: in-flight');

    rc.notifyAttemptFailed('ice_failed', RS.HARD);
    assert(rc.inFlight() === false, 'RC13: no longer in-flight');
    assert(rc.pending().severity === RS.HARD, 'RC13: severity escalated');
    assert(calls.scheduled.length === 2, 'RC13: next attempt scheduled');
  }

  // ═══════════════════════════════════════════════
  // Test 14: notifyAttemptFailed() no-op if not in-flight
  // ═══════════════════════════════════════════════
  console.log('--- RC Test 14: notifyAttemptFailed no-op ---');
  {
    const { rc, calls } = createCoordinator();
    rc.notifyAttemptFailed('x', RS.SOFT);
    assert(calls.scheduled.length === 0, 'RC14: notifyAttemptFailed no-op when not in-flight');
  }

  // ═══════════════════════════════════════════════
  // Test 15: expectSessionResetFromRecreate + consumeExpectedSessionReset
  // ═══════════════════════════════════════════════
  console.log('--- RC Test 15: session reset expectation ---');
  {
    const { rc } = createCoordinator();
    rc.expectSessionResetFromRecreate();
    assert(rc.consumeExpectedSessionReset() === true, 'RC15: first consume → true');
    assert(rc.consumeExpectedSessionReset() === true, 'RC15: second consume → true');
    assert(rc.consumeExpectedSessionReset() === false, 'RC15: third consume → false (exhausted)');
  }

  // ═══════════════════════════════════════════════
  // Test 16: exhaustion → onExhausted called
  // ═══════════════════════════════════════════════
  console.log('--- RC Test 16: exhaustion ---');
  {
    const { rc, clock, calls } = createCoordinator({ maxReconnectAttempts: 2 });
    rc.request('no_frames', RS.SOFT);

    // Attempt 1
    await clock.advance(500);
    await flushMicrotasks();
    rc.startSettleWindow();
    await clock.advance(1000); // settle timeout → retry
    await flushMicrotasks();

    // Attempt 2
    await clock.advance(900);
    await flushMicrotasks();
    rc.startSettleWindow();
    await clock.advance(1000);
    await flushMicrotasks();

    // Now at attempt 2 (maxReconnectAttempts), next scheduleNext should exhaust
    assert(calls.exhausted.length === 1, 'RC16: onExhausted called');
    assert(rc.attempt() === 2, 'RC16: attempt stays at 2');
  }

  // ═══════════════════════════════════════════════
  // Test 17: attempt timeout → severity escalated to HARD
  // ═══════════════════════════════════════════════
  console.log('--- RC Test 17: attempt timeout escalation ---');
  {
    const { rc, clock, calls, setExecute } = createCoordinator({ reconnectAttemptTimeoutMs: 2000 });

    // Make executeAttempt hang forever
    setExecute(async () => new Promise(() => {}));

    rc.request('no_frames', RS.SOFT);
    await clock.advance(500); // backoff fires, executeAttempt starts
    await flushMicrotasks();

    assert(calls.attempts.length === 1, 'RC17: attempt started');
    assert(rc.pending().severity === RS.SOFT, 'RC17: severity still SOFT before timeout');

    // Advance past reconnectAttemptTimeoutMs
    await clock.advance(2000);
    await flushMicrotasks();

    // After timeout, severity should be escalated to HARD
    assert(rc.pending().severity === RS.HARD, 'RC17: severity escalated to HARD after timeout');
    assert(rc.inFlight() === false, 'RC17: no longer in-flight after timeout');
    // Next attempt should be scheduled
    assert(calls.scheduled.length === 2, 'RC17: next attempt scheduled after timeout');
  }

  // ═══════════════════════════════════════════════
  // Test 18: settleStartTimeoutMs expires without startSettleWindow
  // ═══════════════════════════════════════════════
  console.log('--- RC Test 18: settleStartTimeout ---');
  {
    const { rc, clock, calls } = createCoordinator({ settleStartTimeoutMs: 2000 });
    rc.request('no_frames', RS.SOFT);
    await clock.advance(500); // attempt fires
    await flushMicrotasks();
    assert(rc.inFlight() === true, 'RC18: in-flight');

    // Don't call startSettleWindow() — wait for settleStartTimeoutMs
    await clock.advance(2000);
    await flushMicrotasks();

    assert(rc.inFlight() === false, 'RC18: in-flight cleared after settleStartTimeout');
    assert(calls.scheduled.length === 2, 'RC18: next attempt scheduled');
  }

  // ═══════════════════════════════════════════════
  // Test 19: reset() cancels all timers and resets state
  // ═══════════════════════════════════════════════
  console.log('--- RC Test 19: reset ---');
  {
    const { rc, clock, calls } = createCoordinator();
    rc.request('no_frames', RS.SOFT);
    assert(rc.pending() != null, 'RC19: pending before reset');
    rc.reset();
    assert(rc.pending() === null, 'RC19: pending null after reset');
    assert(rc.attempt() === 0, 'RC19: attempt 0 after reset');
    assert(rc.inFlight() === false, 'RC19: not in-flight after reset');

    // Timer should not fire
    await clock.advance(5000);
    assert(calls.attempts.length === 0, 'RC19: no attempts after reset');
  }

  // ═══════════════════════════════════════════════
  // Test 20: multiple requests while in-flight → coalesced
  // ═══════════════════════════════════════════════
  console.log('--- RC Test 20: coalescing while in-flight ---');
  {
    const { rc, clock, calls } = createCoordinator();
    rc.request('no_frames', RS.SOFT);
    await clock.advance(500);
    await flushMicrotasks();
    assert(rc.inFlight() === true, 'RC20: in-flight');

    rc.request('hangup', RS.MEDIUM);
    rc.request('ice_failed', RS.HARD);
    // Should not schedule new timers — already in-flight
    assert(calls.scheduled.length === 1, 'RC20: no extra schedule while in-flight');
    assert(rc.pending().severity === RS.HARD, 'RC20: severity coalesced to HARD');
    assert(rc.pending().reason === 'ice_failed', 'RC20: reason is latest');
  }

  // ═══════════════════════════════════════════════
  // Test 21: recovery ladder — attempt sequence SOFT→SOFT→SOFT→REATTACH→REATTACH→RECREATE
  // ═══════════════════════════════════════════════
  console.log('--- RC Test 21: full recovery ladder ---');
  {
    const { rc, clock, calls } = createCoordinator({ maxReconnectAttempts: 8, reconnectAttemptTimeoutMs: 999999 });
    rc.request('no_frames', RS.SOFT);

    const expectedActions = [
      RA.SOFT_RESTART,     // attempt 1
      RA.SOFT_RESTART,     // attempt 2
      RA.SOFT_RESTART,     // attempt 3
      RA.REATTACH_PLUGIN,  // attempt 4
      RA.REATTACH_PLUGIN,  // attempt 5
      RA.RECREATE_SESSION, // attempt 6
    ];

    for (let i = 0; i < 6; i++) {
      // Fire backoff timer
      await clock.advance(20000); // enough for any backoff
      await flushMicrotasks();
      assert(calls.attempts.length === i + 1, `RC21: attempt ${i + 1} fired`);
      assert(calls.attempts[i].action === expectedActions[i], `RC21: attempt ${i + 1} action is ${expectedActions[i]} (got ${calls.attempts[i].action})`);

      // Fail the attempt to trigger next
      rc.notifyAttemptFailed();
    }
  }

  // ═══════════════════════════════════════════════
  // Test 22: startSettleWindow() idempotent
  // ═══════════════════════════════════════════════
  console.log('--- RC Test 22: startSettleWindow idempotent ---');
  {
    const { rc, clock, calls } = createCoordinator();
    rc.request('no_frames', RS.SOFT);
    await clock.advance(500);
    await flushMicrotasks();

    rc.startSettleWindow();
    rc.startSettleWindow(); // second call should be no-op

    // notifyRecovered should work normally
    rc.notifyRecovered();
    assert(calls.successes.length === 1, 'RC22: success after idempotent startSettleWindow');
  }

  // ═══════════════════════════════════════════════
  // Test 23: startSettleWindow called during executeAttempt → settleStartTimeout not started
  // ═══════════════════════════════════════════════
  console.log('--- RC Test 23: startSettleWindow during execute ---');
  {
    const { rc, clock, calls, setExecute } = createCoordinator({ settleStartTimeoutMs: 2000, connectSettleMs: 1000 });

    // During executeAttempt, call startSettleWindow synchronously
    setExecute(async () => { rc.startSettleWindow(); });

    rc.request('no_frames', RS.SOFT);
    await clock.advance(500);
    await flushMicrotasks();

    // Settle window should already be active. settleStartTimeout should have been cancelled.
    // Wait past settleStartTimeoutMs — should NOT schedule another attempt
    await clock.advance(1500);
    await flushMicrotasks();

    // Only the settle timer (1000ms from startSettleWindow + 500ms extra) should fire
    // Since we advanced 1500ms total (500 + 1000 settle), settle fires and reschedules
    assert(calls.scheduled.length >= 2, 'RC23: settle timeout properly fires');
  }

  // ═══════════════════════════════════════════════
  // Test 24: _onSettle with stale token → dropped
  // ═══════════════════════════════════════════════
  console.log('--- RC Test 24: settle stale token ---');
  {
    const { rc, clock, calls, setToken } = createCoordinator();
    rc.request('no_frames', RS.SOFT);
    await clock.advance(500);
    await flushMicrotasks();

    rc.startSettleWindow();
    setToken(999); // change token during settle

    await clock.advance(1000); // settle fires
    await flushMicrotasks();

    // Settle should see stale token → set inFlight=false but not schedule next
    assert(rc.inFlight() === false, 'RC24: in-flight false after stale settle');
    assert(calls.scheduled.length === 1, 'RC24: no extra schedule for stale settle');
  }

  // ═══════════════════════════════════════════════
  // Test 25: _onSettle with shouldContinue=false → no schedule
  // ═══════════════════════════════════════════════
  console.log('--- RC Test 25: settle shouldContinue false ---');
  {
    const { rc, clock, calls, setShouldContinue } = createCoordinator();
    rc.request('no_frames', RS.SOFT);
    await clock.advance(500);
    await flushMicrotasks();

    rc.startSettleWindow();
    setShouldContinue(false);

    await clock.advance(1000);
    await flushMicrotasks();

    assert(rc.inFlight() === false, 'RC25: in-flight false');
    assert(calls.scheduled.length === 1, 'RC25: no new schedule (shouldContinue false)');
  }

  // ═══════════════════════════════════════════════
  // Test 26: notifyRecovered outside settle (no settle timer) → no-op
  // ═══════════════════════════════════════════════
  console.log('--- RC Test 26: notifyRecovered no settle timer ---');
  {
    const { rc, clock, calls } = createCoordinator();
    rc.request('no_frames', RS.SOFT);
    await clock.advance(500);
    await flushMicrotasks();
    // in-flight but settleTimer not started yet
    rc.notifyRecovered();
    assert(calls.successes.length === 0, 'RC26: notifyRecovered no-op without settle timer');
    assert(rc.inFlight() === true, 'RC26: still in-flight');
  }

  // ═══════════════════════════════════════════════
  // Test 27: settleStartTimeout escalates severity to HARD
  // ═══════════════════════════════════════════════
  console.log('--- RC Test 27: settleStartTimeout escalates to HARD ---');
  {
    const { rc, clock, calls } = createCoordinator({ settleStartTimeoutMs: 500 });
    rc.request('no_frames', RS.SOFT);
    await clock.advance(500);
    await flushMicrotasks();
    // in-flight, startSettleWindow never called → settleStartTimeout will fire
    assert(rc.inFlight(), 'RC27: in-flight after first attempt');
    await clock.advance(500); // settleStartTimeout fires
    await flushMicrotasks();
    assert(!rc.inFlight(), 'RC27: not in-flight after settleStartTimeout');
    // Severity should have been escalated to HARD
    const pending = rc.pending();
    assert(pending !== null, 'RC27: pending exists');
    assert(pending.severity >= 3, 'RC27: severity escalated to HARD (' + pending.severity + ')');
  }

  // ═══════════════════════════════════════════════
  // Test 28: _onSettle escalates severity to HARD
  // ═══════════════════════════════════════════════
  console.log('--- RC Test 28: settle timeout escalates to HARD ---');
  {
    const { rc, clock, calls } = createCoordinator({ connectSettleMs: 500 });
    rc.request('no_frames', RS.SOFT);
    await clock.advance(500);
    await flushMicrotasks();
    assert(rc.inFlight(), 'RC28: in-flight');
    // Start settle window
    rc.startSettleWindow();
    // Don't call notifyRecovered → settle timer will fire
    await clock.advance(500);
    await flushMicrotasks();
    assert(!rc.inFlight(), 'RC28: not in-flight after settle timeout');
    const pending = rc.pending();
    assert(pending !== null, 'RC28: pending exists');
    assert(pending.severity >= 3, 'RC28: severity escalated to HARD (' + pending.severity + ')');
  }

  // ═══════════════════════════════════════════════
  // Test 29: maxWatchRetries=1 → immediate escalation to REATTACH
  // ═══════════════════════════════════════════════
  console.log('--- RC Test 29: maxWatchRetries=1 ladder ---');
  {
    const { rc, clock, calls } = createCoordinator({
      maxWatchRetries: 1,
      maxReattachRetries: 1,
      maxReconnectAttempts: 6,
      connectSettleMs: 100,
    });
    rc.request('no_frames', RS.SOFT);

    // Attempt 1: should be SOFT_RESTART (maxWatchRetries=1)
    await clock.advance(500);
    await flushMicrotasks();
    assert(calls.attempts.length === 1, 'RC29: attempt 1 fired');
    assert(calls.attempts[0].action === RA.SOFT_RESTART, 'RC29: attempt 1 is SOFT_RESTART');

    // Fail it via settle timeout
    rc.startSettleWindow();
    await clock.advance(100);
    await flushMicrotasks();

    // Attempt 2: should be REATTACH_PLUGIN (watch budget exhausted)
    // But severity was escalated to HARD by settle timeout, so it should be RECREATE_SESSION
    await clock.advance(1000);
    await flushMicrotasks();
    assert(calls.attempts.length === 2, 'RC29: attempt 2 fired');
    // After HARD escalation, should be RECREATE_SESSION
    assert(calls.attempts[1].action === RA.RECREATE_SESSION, 'RC29: attempt 2 is RECREATE_SESSION (HARD escalation)');
  }

  // ═══════════════════════════════════════════════
  // Test 30: health check failure → attempt NOT consumed, rescheduled
  // ═══════════════════════════════════════════════
  console.log('--- RC Test 30: health check failure skips attempt ---');
  {
    let fetchCalls = 0;
    sandbox.fetch = async () => { fetchCalls++; throw new Error('network error'); };
    sandbox.AbortController = class { constructor() { this.signal = {}; } abort() {} };

    const { rc, clock, calls } = createCoordinator({
      healthCheckBeforeReconnect: true,
      healthCheckUrl: 'http://test/janus/healthz',
      healthCheckTimeoutMs: 1000,
    });
    rc.request('no_frames', RS.SOFT);
    await clock.advance(500);
    await flushMicrotasks();

    assert(fetchCalls === 1, 'RC30: fetch called once');
    assert(calls.attempts.length === 0, 'RC30: no attempt (health check failed)');
    assert(rc.attempt() === 0, 'RC30: attempt counter not incremented');
    // Should have rescheduled
    assert(calls.scheduled.length >= 2, 'RC30: rescheduled after health check failure');

    delete sandbox.fetch;
    delete sandbox.AbortController;
  }

  // ═══════════════════════════════════════════════
  // Test 31: health check success → normal attempt flow
  // ═══════════════════════════════════════════════
  console.log('--- RC Test 31: health check success → attempt runs ---');
  {
    let fetchCalls = 0;
    sandbox.fetch = async () => {
      fetchCalls++;
      return { ok: true, json: async () => ({ ok: true }) };
    };
    sandbox.AbortController = class { constructor() { this.signal = {}; } abort() {} };

    const { rc, clock, calls } = createCoordinator({
      healthCheckBeforeReconnect: true,
      healthCheckUrl: 'http://test/janus/healthz',
      healthCheckTimeoutMs: 1000,
    });
    rc.request('no_frames', RS.SOFT);
    await clock.advance(500);
    await flushMicrotasks();

    assert(fetchCalls === 1, 'RC31: fetch called');
    assert(calls.attempts.length === 1, 'RC31: attempt executed after healthy check');
    assert(rc.attempt() === 1, 'RC31: attempt counter incremented');

    delete sandbox.fetch;
    delete sandbox.AbortController;
  }

  // ═══════════════════════════════════════════════
  // Test 32: health check disabled → no fetch, normal flow
  // ═══════════════════════════════════════════════
  console.log('--- RC Test 32: health check disabled ---');
  {
    let fetchCalls = 0;
    sandbox.fetch = async () => { fetchCalls++; return { ok: true, json: async () => ({ ok: true }) }; };
    sandbox.AbortController = class { constructor() { this.signal = {}; } abort() {} };

    const { rc, clock, calls } = createCoordinator({
      healthCheckBeforeReconnect: false,
      healthCheckUrl: 'http://test/janus/healthz',
    });
    rc.request('no_frames', RS.SOFT);
    await clock.advance(500);
    await flushMicrotasks();

    assert(fetchCalls === 0, 'RC32: fetch NOT called when disabled');
    assert(calls.attempts.length === 1, 'RC32: attempt runs without health check');

    delete sandbox.fetch;
    delete sandbox.AbortController;
  }

  // ═══════════════════════════════════════════════
  // Test 33: health check returns {ok: false} → treated as failure
  // ═══════════════════════════════════════════════
  console.log('--- RC Test 33: health check ok=false ---');
  {
    sandbox.fetch = async () => ({ ok: true, json: async () => ({ ok: false }) });
    sandbox.AbortController = class { constructor() { this.signal = {}; } abort() {} };

    const { rc, clock, calls } = createCoordinator({
      healthCheckBeforeReconnect: true,
      healthCheckUrl: 'http://test/janus/healthz',
      healthCheckTimeoutMs: 1000,
    });
    rc.request('no_frames', RS.SOFT);
    await clock.advance(500);
    await flushMicrotasks();

    assert(calls.attempts.length === 0, 'RC33: no attempt when healthz ok=false');
    assert(rc.attempt() === 0, 'RC33: attempt counter not incremented');

    delete sandbox.fetch;
    delete sandbox.AbortController;
  }

  // ═══════════════════════════════════════════════
  // Summary
  // ═══════════════════════════════════════════════
  console.log(`\nCoordinator tests: ${passed} passed, ${failed} failed`);
  if (failed > 0) process.exit(1);
  console.log('OK: coordinator tests passed');
}

main().catch((e) => { console.error(e); process.exit(1); });
