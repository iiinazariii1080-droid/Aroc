/* Unit tests for App-layer services: TimerCoordinator, WatchdogService, RecoveryMap, StatsService (Node, no browser). */
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
  let now = 1000; // start at 1000 to avoid 0-edge issues
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

  function advance(ms) {
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
        if (t && typeof t.fn === 'function') t.fn();
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
    debugSnapshot: () => ({ timeouts: timeouts.size, intervals: intervals.size }),
  };
}

function main() {
  const root = path.resolve(__dirname, '..');

  const sandbox = vm.createContext({
    window: {},
    console,
    Math: Math,
  });
  sandbox.Math.random = () => 0.5;
  sandbox.Janus = { randomString: () => 'deadbeef' };

  // Load in dependency order
  loadScript(sandbox, path.join(root, 'player', 'ns.js'));
  loadScript(sandbox, path.join(root, 'player', 'core', 'player_state.js'));
  loadScript(sandbox, path.join(root, 'player', 'core', 'codes.js'));
  loadScript(sandbox, path.join(root, 'player', 'core', 'domain_events.js'));
  loadScript(sandbox, path.join(root, 'player', 'core', 'connection_policy.js'));
  loadScript(sandbox, path.join(root, 'player', 'core', 'backoff.js'));
  loadScript(sandbox, path.join(root, 'player', 'core', 'recovery_policy.js'));
  loadScript(sandbox, path.join(root, 'player', 'app', 'recovery_map.js'));
  loadScript(sandbox, path.join(root, 'player', 'app', 'timer_coordinator.js'));
  loadScript(sandbox, path.join(root, 'player', 'app', 'watchdog_service.js'));

  const AP = sandbox.window.AutonomousPlayer;
  const RS = AP.Core.RecoverySeverity;
  const RR = AP.Core.RecoveryReason;

  // ═══════════════════════════════════════════════
  // TimerCoordinator
  // ═══════════════════════════════════════════════
  console.log('--- TimerCoordinator ---');

  {
    const clock = createFakeClock();
    const tc = new AP.App.TimerCoordinator(clock);

    // set() + callback fires
    let fired = false;
    tc.set('t1', () => { fired = true; }, 500);
    assert(tc.has('t1') === true, 'TC: has("t1") true after set');
    assert(fired === false, 'TC: not fired before advance');
    clock.advance(500);
    assert(fired === true, 'TC: callback fires at correct time');
    assert(tc.has('t1') === false, 'TC: key removed after fire');

    // set() same key replaces previous
    let fired1 = false, fired2 = false;
    tc.set('replace', () => { fired1 = true; }, 1000);
    tc.set('replace', () => { fired2 = true; }, 500);
    clock.advance(500);
    assert(fired2 === true, 'TC: replacement callback fires');
    clock.advance(500);
    assert(fired1 === false, 'TC: original callback never fires after replacement');

    // clear() cancels pending timeout
    let firedClear = false;
    tc.set('c1', () => { firedClear = true; }, 500);
    assert(tc.has('c1') === true, 'TC: has before clear');
    tc.clear('c1');
    assert(tc.has('c1') === false, 'TC: has false after clear');
    clock.advance(600);
    assert(firedClear === false, 'TC: cleared callback never fires');

    // clear() on non-existent key → no-op (no throw)
    tc.clear('nonexistent');

    // clearPrefix()
    let pA = false, pB = false, other = false;
    tc.set('trackMute:v0', () => { pA = true; }, 1000);
    tc.set('trackMute:v1', () => { pB = true; }, 1000);
    tc.set('iceGrace', () => { other = true; }, 1000);
    assert(tc.has('trackMute:v0'), 'TC: has trackMute:v0');
    assert(tc.has('trackMute:v1'), 'TC: has trackMute:v1');
    tc.clearPrefix('trackMute:');
    assert(tc.has('trackMute:v0') === false, 'TC: trackMute:v0 cleared by prefix');
    assert(tc.has('trackMute:v1') === false, 'TC: trackMute:v1 cleared by prefix');
    assert(tc.has('iceGrace') === true, 'TC: iceGrace not cleared by prefix');
    clock.advance(1000);
    assert(pA === false, 'TC: prefixed callback A never fires');
    assert(pB === false, 'TC: prefixed callback B never fires');
    assert(other === true, 'TC: non-prefixed callback fires normally');

    // clearAll()
    let allA = false, allB = false;
    tc.set('x', () => { allA = true; }, 500);
    tc.set('y', () => { allB = true; }, 500);
    tc.clearAll();
    assert(tc.has('x') === false, 'TC: has x false after clearAll');
    assert(tc.has('y') === false, 'TC: has y false after clearAll');
    clock.advance(600);
    assert(allA === false, 'TC: x callback never fires after clearAll');
    assert(allB === false, 'TC: y callback never fires after clearAll');
  }

  // ═══════════════════════════════════════════════
  // WatchdogService
  // ═══════════════════════════════════════════════
  console.log('--- WatchdogService ---');

  {
    const clock = createFakeClock();
    const cfg = { noFrameThresholdMs: 3000, watchdogTickMs: 1000, minAcceptableFps: 5, fpsDropThresholdMs: 3000 };
    let timeoutCalls = [];
    let fpsCalls = [];

    const wd = new AP.App.WatchdogService(cfg, clock, (age) => timeoutCalls.push(age), (fps) => fpsCalls.push(fps));

    // start() + no frames → onTimeout called
    wd.start();
    clock.advance(4000); // tick at 1000, 2000, 3000, 4000
    assert(timeoutCalls.length > 0, 'WD: onTimeout called when no frames');
    assert(timeoutCalls[0] > 3000, 'WD: timeout age > noFrameThresholdMs');

    // updateFrameTime() resets frame age
    timeoutCalls = [];
    wd.updateFrameTime();
    clock.advance(2000);
    assert(timeoutCalls.length === 0, 'WD: no timeout when frames are recent');

    // getLastFrameAgeMs()
    const age1 = wd.getLastFrameAgeMs();
    assert(age1 >= 2000, 'WD: getLastFrameAgeMs >= 2000 after 2s');

    wd.updateFrameTime();
    const age2 = wd.getLastFrameAgeMs();
    assert(age2 < 100, 'WD: getLastFrameAgeMs near 0 after updateFrameTime');

    // getCurrentFps() with empty ring → 0
    const freshWd = new AP.App.WatchdogService(cfg, clock, () => {}, () => {});
    assert(freshWd.getCurrentFps() === 0, 'WD: getCurrentFps with empty ring → 0');

    // getCurrentFps() with 1 frame → 0 (need at least 2)
    freshWd.updateFrameTime();
    assert(freshWd.getCurrentFps() === 0, 'WD: getCurrentFps with 1 frame → 0');

    // getCurrentFps() with multiple frames → correct FPS
    const clock2 = createFakeClock();
    const wd2 = new AP.App.WatchdogService(cfg, clock2, () => {}, () => {});
    // Simulate 30fps for 1 second: 30 frames at ~33ms intervals
    for (let i = 0; i < 30; i++) {
      wd2.updateFrameTime();
      clock2.advance(33);
    }
    const fps = wd2.getCurrentFps();
    assert(fps > 25 && fps < 35, `WD: getCurrentFps ~ 30 (got ${fps.toFixed(1)})`);

    // FPS ring wraps around (>60 frames)
    const clock3 = createFakeClock();
    const wd3 = new AP.App.WatchdogService(cfg, clock3, () => {}, () => {});
    for (let i = 0; i < 100; i++) {
      wd3.updateFrameTime();
      clock3.advance(33);
    }
    const fpsWrapped = wd3.getCurrentFps();
    assert(fpsWrapped > 25 && fpsWrapped < 35, `WD: FPS after ring wrap ~ 30 (got ${fpsWrapped.toFixed(1)})`);

    // FPS drop detection — sustained low FPS fires onFpsDrop
    const clock4 = createFakeClock();
    let fpsDropCalls = [];
    const wd4 = new AP.App.WatchdogService(cfg, clock4, () => {}, (fps) => fpsDropCalls.push(fps));
    wd4.start();

    // Fill the ring with enough frames so getCurrentFps() returns something
    for (let i = 0; i < 10; i++) {
      wd4.updateFrameTime();
      clock4.advance(500); // 2 fps (below minAcceptableFps=5)
    }
    // Advance more to exceed fpsDropThresholdMs (3000ms)
    for (let i = 0; i < 10; i++) {
      wd4.updateFrameTime();
      clock4.advance(500);
    }
    assert(fpsDropCalls.length > 0, 'WD: onFpsDrop fired after sustained low FPS');

    // onFpsDrop only fires once (latch)
    const countBefore = fpsDropCalls.length;
    for (let i = 0; i < 5; i++) {
      wd4.updateFrameTime();
      clock4.advance(500);
    }
    assert(fpsDropCalls.length === countBefore, 'WD: onFpsDrop latch — does not fire again');

    // FPS recovers → latch reset
    const clock5 = createFakeClock();
    let dropCalls5 = [];
    const wd5 = new AP.App.WatchdogService(cfg, clock5, () => {}, (fps) => dropCalls5.push(fps));
    wd5.start();
    // Simulate low fps
    for (let i = 0; i < 10; i++) {
      wd5.updateFrameTime();
      clock5.advance(500);
    }
    // Now simulate high fps recovery: many frames quickly
    for (let i = 0; i < 60; i++) {
      wd5.updateFrameTime();
      clock5.advance(33); // ~30fps
    }
    // The drop latch should have reset because FPS recovered above threshold

    // stop() clears timer, resets drop state
    wd5.stop();
    assert(wd5._timer === null, 'WD: stop() clears timer');
    assert(wd5._fpsDropSince === 0, 'WD: stop() resets fpsDropSince');
    assert(wd5._fpsDropFired === false, 'WD: stop() resets fpsDropFired');

    // resetFpsTracking()
    const clock6 = createFakeClock();
    const wd6 = new AP.App.WatchdogService(cfg, clock6, () => {}, () => {});
    for (let i = 0; i < 30; i++) wd6.updateFrameTime();
    wd6.resetFpsTracking();
    assert(wd6._fpsRing.length === 0, 'WD: resetFpsTracking clears ring');
    assert(wd6._fpsRingIdx === 0, 'WD: resetFpsTracking resets index');
    assert(wd6._fpsRingFull === false, 'WD: resetFpsTracking resets full flag');
    assert(wd6.getCurrentFps() === 0, 'WD: getCurrentFps after reset → 0');

    // start() idempotent
    const clock7 = createFakeClock();
    let callCount = 0;
    const wd7 = new AP.App.WatchdogService(cfg, clock7, () => callCount++, null);
    wd7.start();
    wd7.start(); // second call should be ignored
    clock7.advance(10000);
    // If start() registered a second interval, callCount would be doubled
    const expected = Math.floor(10000 / cfg.watchdogTickMs);
    assert(callCount <= expected + 1, 'WD: start() idempotent — no double interval');

    // No-fps-drop callback → onFpsDrop is null, no crash
    const clock8 = createFakeClock();
    const wd8 = new AP.App.WatchdogService(cfg, clock8, () => {});
    wd8.start();
    wd8.updateFrameTime();
    clock8.advance(5000);
    // Should not throw

    // resetAfterTabResume() resets lastFrameAt + FPS ring
    const clock9 = createFakeClock();
    const wd9 = new AP.App.WatchdogService(cfg, clock9, () => {}, () => {});
    wd9.updateFrameTime();
    for (let i = 0; i < 10; i++) { clock9.advance(33); wd9.updateFrameTime(); }
    clock9.advance(5000); // stale frame age
    assert(wd9.getLastFrameAgeMs(clock9.nowMs()) >= 5000, 'WD: age stale before resetAfterTabResume');
    assert(wd9.getCurrentFps(clock9.nowMs()) > 0, 'WD: FPS > 0 before resetAfterTabResume');
    wd9.resetAfterTabResume();
    assert(wd9.getLastFrameAgeMs(clock9.nowMs()) === 0, 'WD: age 0 after resetAfterTabResume');
    assert(wd9.getCurrentFps(clock9.nowMs()) === 0, 'WD: FPS 0 after resetAfterTabResume (ring cleared)');
    assert(wd9._fpsRing.length === 0, 'WD: ring empty after resetAfterTabResume');
  }

  // ═══════════════════════════════════════════════
  // RecoveryMap (recovery_map.js)
  // ═══════════════════════════════════════════════
  console.log('--- RecoveryMap ---');

  {
    const RecoveryPolicy = AP.App.RecoveryPolicy;

    // All 13 known reasons have entries
    const reasons = [
      RR.WEBRTC_DOWN, RR.ICE_FAILED, RR.ICE_DISCONNECTED_GRACE, RR.HANGUP,
      RR.NO_FRAMES, RR.TRACK_MUTED, RR.JANUS_ERROR, RR.ALREADY_WATCHING,
      RR.SESSION_RESET, RR.FPS_DROP, RR.VIDEO_STALLED, RR.TAB_RESUME_STALE,
      RR.NETWORK_RESTORED,
    ];
    for (const r of reasons) {
      const sev = RecoveryPolicy.DefaultSeverityByReason[r];
      assert(sev != null, `RecoveryMap: has entry for ${r}`);
      assert(sev >= RS.SOFT && sev <= RS.HARD, `RecoveryMap: severity for ${r} is valid (got ${sev})`);
    }

    // Specific expected severities
    assert(RecoveryPolicy.DefaultSeverityByReason[RR.ICE_FAILED] === RS.HARD, 'RecoveryMap: ICE_FAILED → HARD');
    assert(RecoveryPolicy.DefaultSeverityByReason[RR.SESSION_RESET] === RS.HARD, 'RecoveryMap: SESSION_RESET → HARD');
    assert(RecoveryPolicy.DefaultSeverityByReason[RR.ALREADY_WATCHING] === RS.HARD, 'RecoveryMap: ALREADY_WATCHING → HARD');
    assert(RecoveryPolicy.DefaultSeverityByReason[RR.WEBRTC_DOWN] === RS.MEDIUM, 'RecoveryMap: WEBRTC_DOWN → MEDIUM');
    assert(RecoveryPolicy.DefaultSeverityByReason[RR.NO_FRAMES] === RS.MEDIUM, 'RecoveryMap: NO_FRAMES → MEDIUM');

    // defaultSeverityForReason()
    assert(RecoveryPolicy.defaultSeverityForReason(RR.ICE_FAILED) === RS.HARD, 'defaultSeverity: ICE_FAILED → HARD');
    assert(RecoveryPolicy.defaultSeverityForReason(RR.NO_FRAMES) === RS.MEDIUM, 'defaultSeverity: NO_FRAMES → MEDIUM');

    // Unknown reason → fallback (SOFT)
    assert(RecoveryPolicy.defaultSeverityForReason('UNKNOWN_REASON') === RS.SOFT, 'defaultSeverity: unknown → SOFT');

    // Null reason → fallback
    assert(RecoveryPolicy.defaultSeverityForReason(null) === RS.SOFT, 'defaultSeverity: null → SOFT');

    // Custom fallback
    assert(RecoveryPolicy.defaultSeverityForReason('UNKNOWN', RS.HARD) === RS.HARD, 'defaultSeverity: unknown + custom fallback');
  }

  // ═══════════════════════════════════════════════
  // StatsService (with mock PeerConnection)
  // ═══════════════════════════════════════════════
  console.log('--- StatsService ---');

  // Need to load StatsService
  loadScript(sandbox, path.join(root, 'player', 'app', 'stats_service.js'));

  {
    const clock = createFakeClock();
    const log = { debug: () => {}, info: () => {}, warn: () => {}, error: () => {} };
    const cfg = { statsIntervalMs: 1000 };

    // Mock streaming port with no peer connection
    const streamingNoPc = { getPeerConnection: () => null };
    const stats1 = new AP.App.StatsService(cfg, clock, log, streamingNoPc);

    let lastText = '';
    stats1.start((t) => { lastText = t; });
    // _collectText is async but runs synchronously in our fake context because getPeerConnection returns null.
    // We need to wait for it. In fake clock, advance a tick.
    clock.advance(1);
    // Note: since _collectText is async, the first tick may be delayed.
    // Force advance past the interval.
    clock.advance(1000);
    // lastText may be 'No peer connection' or empty depending on async timing
    // In Node, Promise microtasks resolve between ticks
    // Let's validate stop() works
    stats1.stop();
    assert(stats1._timer === null, 'Stats: stop() clears timer');
    assert(stats1._running === false, 'Stats: stop() sets running false');

    // start() idempotent
    const stats2 = new AP.App.StatsService(cfg, clock, log, streamingNoPc);
    stats2.start(() => {});
    stats2.start(() => {}); // should not create second interval
    assert(stats2._running === true, 'Stats: still running after double start');
    stats2.stop();

    // setJoystickService()
    const stats3 = new AP.App.StatsService(cfg, clock, log, streamingNoPc);
    assert(stats3._joystick === null, 'Stats: initial joystick is null');
    const mockJoy = { joyE2eMs: 25, joyJitterMs: 3 };
    stats3.setJoystickService(mockJoy);
    assert(stats3._joystick === mockJoy, 'Stats: setJoystickService sets reference');
    stats3.setJoystickService(null);
    assert(stats3._joystick === null, 'Stats: setJoystickService(null) clears');

    // Mock PeerConnection with stats report for delta computation
    const makeReport = (bytesReceived, packetsLost, packetsReceived, framesPerSecond, jitterBufferDelay, jitterBufferEmittedCount) => {
      const stats = new Map();
      stats.set('inbound-rtp-1', {
        type: 'inbound-rtp',
        kind: 'video',
        bytesReceived,
        packetsLost,
        packetsReceived,
        framesPerSecond,
        jitterBufferDelay,
        jitterBufferEmittedCount,
      });
      stats.set('candidate-pair-1', {
        type: 'candidate-pair',
        state: 'succeeded',
        nominated: true,
        currentRoundTripTime: 0.05,
      });
      return stats;
    };

    let callCount = 0;
    const streamingWithPc = {
      getPeerConnection: () => ({
        getStats: async () => {
          callCount++;
          if (callCount === 1) return makeReport(5000, 0, 100, 30, 0.5, 50);
          return makeReport(15000, 2, 200, 30, 1.2, 110);
        },
      }),
    };

    const clockS = createFakeClock();
    const stats4 = new AP.App.StatsService(cfg, clockS, log, streamingWithPc);
    let collectedTexts = [];
    stats4.start((t) => collectedTexts.push(t));
    // Advance to trigger multiple collections
    // Note: the async tick means we need to advance multiple times
    // For testing _collectText directly:
    stats4.stop();
  }

  // ═══════════════════════════════════════════════
  // Summary
  // ═══════════════════════════════════════════════
  console.log(`\nApp tests: ${passed} passed, ${failed} failed`);
  if (failed > 0) process.exit(1);
  console.log('OK: app tests passed');
}

main();
