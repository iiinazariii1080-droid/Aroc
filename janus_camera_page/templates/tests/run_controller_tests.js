/* Minimal deterministic tests for PlayerController state machine (Node, no browser/Janus).
 * Focus: state transitions + reconnect semantics. */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

function assert(cond, msg){
  if (!cond) throw new Error('Assertion failed: ' + (msg || ''));
}

function loadScript(sandbox, filePath){
  const code = fs.readFileSync(filePath, 'utf8');
  vm.runInContext(code, sandbox, { filename: filePath });
}

function createFakeClock(){
  let now = 0;
  let nextId = 1;
  const timeouts = new Map(); // id -> {at, fn}
  const intervals = new Map(); // id -> {nextAt, every, fn}

  function setTimeoutFn(fn, ms){
    const id = nextId++;
    timeouts.set(id, { at: now + Math.max(0, Math.trunc(ms || 0)), fn });
    return id;
  }
  function clearTimeoutFn(id){ timeouts.delete(id); }

  function setIntervalFn(fn, ms){
    const every = Math.max(1, Math.trunc(ms || 1));
    const id = nextId++;
    intervals.set(id, { nextAt: now + every, every, fn });
    return id;
  }
  function clearIntervalFn(id){ intervals.delete(id); }

  async function advance(ms){
    const target = now + Math.max(0, Math.trunc(ms || 0));

    // Drain tasks in chronological order; callbacks see now==scheduled time.
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
          const res = t.fn();
          if (res && typeof res.then === 'function') await res;
        }
      } else {
        const t = intervals.get(nextIntervalId);
        if (t && typeof t.fn === 'function') {
          // reschedule before calling to avoid re-entrancy surprises
          t.nextAt += t.every;
          const res = t.fn();
          if (res && typeof res.then === 'function') await res;
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

async function flushMicrotasks(){
  // Enough for awaiting immediate promises inside async callbacks.
  await Promise.resolve();
  await Promise.resolve();
}

async function main(){
  const root = path.resolve(__dirname, '..');

  const sandbox = vm.createContext({
    window: {},
    console,
    Math: Math,
  });
  sandbox.Math.random = () => 0.5; // deterministic

  loadScript(sandbox, path.join(root, 'player', 'ns.js'));
  loadScript(sandbox, path.join(root, 'player', 'core', 'player_state.js'));
  loadScript(sandbox, path.join(root, 'player', 'core', 'codes.js'));
  loadScript(sandbox, path.join(root, 'player', 'core', 'domain_events.js'));
  loadScript(sandbox, path.join(root, 'player', 'core', 'connection_policy.js'));
  loadScript(sandbox, path.join(root, 'player', 'core', 'state_machine_canonical.js'));
  loadScript(sandbox, path.join(root, 'player', 'core', 'invariants.js'));
  loadScript(sandbox, path.join(root, 'player', 'core', 'fail_closed.js'));
  loadScript(sandbox, path.join(root, 'player', 'core', 'backoff.js'));
  loadScript(sandbox, path.join(root, 'player', 'core', 'recovery_policy.js'));
  loadScript(sandbox, path.join(root, 'player', 'app', 'recovery_map.js'));
  loadScript(sandbox, path.join(root, 'player', 'app', 'reconnect_coordinator.js'));
  loadScript(sandbox, path.join(root, 'player', 'app', 'timer_coordinator.js'));
  loadScript(sandbox, path.join(root, 'player', 'app', 'watchdog_service.js'));
  loadScript(sandbox, path.join(root, 'player', 'app', 'player_controller.js'));

  const AP = sandbox.window.AutonomousPlayer;
  const PlayerState = AP.Core.PlayerState;

  const clock = createFakeClock();

  // Minimal logger
  const log = {
    debug: () => {},
    info: () => {},
    warn: () => {},
    error: () => {},
  };

  // UI stub
  let frameCb = null;
  const ui = {
    startFrameClock: (cb) => { frameCb = cb; },
    bindIntents: () => {},
    render: () => {},
    bindStream: () => {},
    ensurePlaying: async () => ({ ok: true, blocked: false }),
  };

  // Streaming stub (watch() emits STREAMING_OFFER_RECEIVED so reconnect settle window is started and notifyRecovered can reset attempt)
  const streaming = {
    _sink: null,
    setEventSink: function(s, getToken){ this._sink = s; this._getToken = getToken; },
    init: async () => {},
    listStreams: async () => ([{ id: 1 }, { id: 2 }]),
    watch: async function(){ if (this._sink) this._sink({ type: 'STREAMING_OFFER_RECEIVED', payload: {} }); },
    stop: async () => {},
    detach: async () => {},
    recreate: async () => {},
    getInboundStream: () => ({}),
    getPeerConnection: () => null,
  };

  const stats = { start: () => {}, stop: () => {} };
  const rtcConfig = { iceServers: [], iceTransportPolicy: 'all' };

  const cfg = {
    autoplayEnabled: true,
    autoplayForcedMuted: true,
    autonomousEnabled: true,

    // reconnect/backoff
    backoffBaseMs: 500,
    backoffFactor: 1.8,
    backoffMinMs: 250,
    backoffMaxMs: 15000,
    backoffJitterRatio: 0.0,
    maxReconnectAttempts: 4,

    // timing
    watchdogTickMs: 250,
    noFrameThresholdMs: 1500,
    connectSettleMs: 1000,
    iceDisconnectedGraceMs: 500,
    trackMuteRestartMs: 1000,

    // stream selection
    preferStreamId: 1,

    // stats
    statsIntervalMs: 1000,

    // debug
    debug: false,
    debugPanelEnabled: false,
  };

  // ---- Test 1: initial connect, first frame -> PLAYING (first frame drives STREAM_RECOVERED)
  const c1 = new AP.App.PlayerController(cfg, rtcConfig, ui, clock, log, streaming, stats, null);
  await c1.init();
  assert(c1.desiredPlaying === true, 'autoplay sets desiredPlaying');
  assert(c1.state === PlayerState.CONNECTING, 'autoplay starts in CONNECTING');

  assert(typeof frameCb === 'function', 'frame callback installed');
  // Simulate WebRTC connected so isConnected() is true on first frame
  streaming._sink({ type: 'WEBRTC_STATE', payload: { up: true } });
  frameCb();
  assert(c1.state === PlayerState.PLAYING, 'first frame drives STREAM_RECOVERED -> PLAYING');

  // ---- Test 2: recovery attempt, frames during RECONNECTING -> PLAYING
  // Simulate stream lost so isRecovered() is false when reconnect timer fires (otherwise we'd skip to PLAYING).
  streaming._sink({ type: 'WEBRTC_STATE', payload: { up: false } });
  c1.requestRecovery('no_frames', AP.Core.RecoverySeverity.SOFT);
  assert(c1._reconnect.attempt() === 0, 'attempt not incremented before timer');
  assert(c1.state === PlayerState.RECONNECTING, 'scheduling recovery enters RECONNECTING');
  // timer fires at +500ms
  await clock.advance(500);
  await flushMicrotasks();
  assert(c1.state === PlayerState.RECONNECTING, 'after timer fired, should be RECONNECTING');
  assert(c1._reconnect.attempt() === 1, 'attempt incremented on execution');

  // frames resume while reconnecting (simulate WebRTC up again so _isConnected() and transition to PLAYING)
  streaming._sink({ type: 'WEBRTC_STATE', payload: { up: true } });
  await clock.advance(100);
  frameCb();
  assert(c1.state === PlayerState.PLAYING, 'frame during RECONNECTING transitions back to PLAYING');

  // settle window fires at +1000ms from attempt
  await clock.advance(1000);
  await flushMicrotasks();
  assert(c1.state === PlayerState.PLAYING, 'after settle window, remains PLAYING');
  assert(c1._reconnect.attempt() === 0, 'attempt reset after recovery success');

  // ---- Test 3: ERROR is terminal until Retry (no background reschedule)
  const cfg2 = Object.assign({}, cfg, { maxReconnectAttempts: 1 });
  const c2 = new AP.App.PlayerController(cfg2, rtcConfig, ui, clock, log, streaming, stats, null);
  await c2.init();
  streaming._sink({ type: 'WEBRTC_STATE', payload: { up: true } });
  frameCb();
  assert(c2.state === PlayerState.PLAYING, 'c2 enters PLAYING');

  // Simulate stream down so isRecovered() is false when reconnect timer fires (otherwise we'd skip to PLAYING).
  streaming._sink({ type: 'WEBRTC_STATE', payload: { up: false } });

  // trigger recovery but DO NOT emit frames -> should exhaust and end in ERROR
  c2.requestRecovery('no_frames', AP.Core.RecoverySeverity.SOFT);
  await clock.advance(500);
  await flushMicrotasks();
  await clock.advance(1000);
  await flushMicrotasks();

  assert(c2.state === PlayerState.ERROR, 'exhaustion ends in ERROR');
  assert(c2.errCode === AP.Core.PlayerErrorCode.RECONNECT_EXHAUSTED, 'exhaustion sets RECONNECT_EXHAUSTED');
  assert(c2.desiredPlaying === false, 'after exhaustion desiredPlaying false');
  const prev = c2.state;
  c2.requestRecovery('ice_failed', AP.Core.RecoverySeverity.HARD);
  await clock.advance(2000);
  await flushMicrotasks();
  assert(c2.state === prev, 'ERROR does not auto-recover without Retry');

  // ---- Test 4: session token prevents stale connect completion after user Stop
  {
    const clock4 = createFakeClock();
    let frameCb4 = null;

    const ui4 = {
      startFrameClock: (cb) => { frameCb4 = cb; },
      bindIntents: () => {},
      render: () => {},
      bindStream: () => {},
      ensurePlaying: async () => ({ ok: true, blocked: false }),
    };

    let initResolve = null;
    let watchCalls = 0;
    const streaming4 = {
      _sink: null,
      setEventSink: function(s){ this._sink = s; },
      init: async () => new Promise((resolve) => { initResolve = resolve; }),
      listStreams: async () => ([{ id: 1 }]),
      watch: async () => { watchCalls += 1; },
      stop: async () => {},
      detach: async () => {},
      recreate: async () => {},
      getInboundStream: () => ({}),
      getPeerConnection: () => null,
    };

    const c4 = new AP.App.PlayerController(cfg, rtcConfig, ui4, clock4, log, streaming4, stats, null);
    const initP = c4.init();
    await flushMicrotasks();

    // Autoplay sets desiredPlaying + enters CONNECTING, but init is still blocked.
    assert(c4.desiredPlaying === true, 'autoplay desiredPlaying true');
    assert(c4.state === PlayerState.CONNECTING, 'autoplay CONNECTING');
    assert(typeof frameCb4 === 'function', 'frame clock installed');

    // User stops before init completes.
    c4.togglePlay();
    assert(c4.desiredPlaying === false, 'stop sets desiredPlaying false');
    assert(c4.state === PlayerState.IDLE, 'stop transitions to IDLE');

    // Now the old init finishes; it must not resurrect the watch.
    initResolve(true);
    await initP;
    await flushMicrotasks();
    assert(watchCalls === 0, 'stale init does not call watch');
    assert(c4.state === PlayerState.IDLE, 'state remains IDLE');
  }

  // ---- Test 5a: FORCE_ERROR bumps generation via state machine only (P0_02)
  {
    const EventType = AP.Core.EventType;
    const c5a = new AP.App.PlayerController(cfg, rtcConfig, ui, clock, log, streaming, stats, null);
    await c5a.init();
    streaming._sink({ type: 'WEBRTC_STATE', payload: { up: true } });
    frameCb();
    assert(c5a.state === PlayerState.PLAYING, 'c5a PLAYING');
    const tokenBefore = c5a._sessionToken;
    c5a.handleEvent({ type: EventType.FORCE_ERROR, reason: 'test', generation: tokenBefore });
    assert(c5a.state === PlayerState.ERROR, 'FORCE_ERROR -> ERROR');
    assert(c5a._sessionToken === tokenBefore + 1, 'FORCE_ERROR bumps generation exactly once via snapshot');
  }

  // ---- Test 5: handleEvent invalid event -> fail-closed ERROR (SAFETY_LAWS L18)
  {
    const c5 = new AP.App.PlayerController(cfg, rtcConfig, ui, clock, log, streaming, stats, null);
    await c5.init();
    streaming._sink({ type: 'WEBRTC_STATE', payload: { up: true } });
    frameCb();
    assert(c5.state === PlayerState.PLAYING, 'c5 PLAYING');
    c5.handleEvent({ type: 'INVALID_EVENT' });
    assert(c5.state === PlayerState.ERROR, 'invalid event handleEvent -> fail-closed ERROR');
  }

  // ---- Test 6a: FIRST_FRAME_RECEIVED drives firstFrameReceived via state machine only (no direct _firstFrameLatch)
  {
    const EventType = AP.Core.EventType;
    const c6a = new AP.App.PlayerController(cfg, rtcConfig, ui, clock, log, streaming, stats, null);
    await c6a.init();
    assert(c6a.state === PlayerState.CONNECTING, 'c6a CONNECTING');
    assert(c6a._firstFrameLatch === false, 'no frame yet');
    streaming._sink({ type: 'WEBRTC_STATE', payload: { up: true } });
    assert(c6a.webrtcUp === true, 'webrtc up');
    c6a.handleEvent({ type: EventType.FIRST_FRAME_RECEIVED, generation: c6a._sessionToken });
    assert(c6a._firstFrameLatch === true, 'FIRST_FRAME_RECEIVED applied via snapshot');
    assert(c6a.state === PlayerState.CONNECTING, 'state still CONNECTING until STREAM_RECOVERED');
    c6a.handleEvent({ type: EventType.STREAM_RECOVERED, generation: c6a._sessionToken });
    assert(c6a.state === PlayerState.PLAYING, 'STREAM_RECOVERED -> PLAYING');
  }

  // ---- Test 6b: critical action throws -> fail-closed ERROR (P1_01)
  {
    const PlayerErrorCode = AP.Core.PlayerErrorCode;
    const ActionType = AP.Core.ActionType;
    const EventType = AP.Core.EventType;
    const errors = [];
    const logErr = {
      debug: () => {},
      info: () => {},
      warn: () => {},
      error: (msg, data) => { errors.push({ msg, data }); },
    };
    const uiBindThrows = {
      startFrameClock: (cb) => { frameCb = cb; },
      bindIntents: () => {},
      render: () => {},
      bindStream: () => { throw new Error('bind_failed'); },
      ensurePlaying: async () => ({ ok: true, blocked: false }),
    };
    const c6b = new AP.App.PlayerController(cfg, rtcConfig, uiBindThrows, clock, logErr, streaming, stats, null);
    await c6b.init();
    assert(c6b.state === PlayerState.CONNECTING, 'c6b CONNECTING');
    c6b.handleEvent({ type: EventType.TRACK_READY, generation: c6b._sessionToken });
    assert(c6b.state === PlayerState.ERROR, 'critical action (BIND_STREAM) throw -> fail-closed ERROR');
    assert(c6b.errCode === PlayerErrorCode.ACTION_FAILED, 'ACTION_FAILED set');
    assert(errors.some((e) => e.msg === 'action_executor_error' && e.data && e.data.action === ActionType.BIND_STREAM), 'failure logged');
  }

  // ---- Test 6c: non-critical action throws -> warning logged, no transition to ERROR (P1_01)
  {
    const warnings = [];
    let renderCallCount = 0;
    const logWarn = {
      debug: () => {},
      info: () => {},
      warn: (msg, data) => { warnings.push({ msg, data }); },
      error: () => {},
    };
    const uiRenderThrows = {
      startFrameClock: (cb) => { frameCb = cb; },
      bindIntents: () => {},
      render: () => { if (++renderCallCount > 1) throw new Error('render_failed'); },
      bindStream: () => {},
      ensurePlaying: async () => ({ ok: true, blocked: false }),
    };
    const c6c = new AP.App.PlayerController(cfg, rtcConfig, uiRenderThrows, clock, logWarn, streaming, stats, null);
    await c6c.init();
    streaming._sink({ type: 'WEBRTC_STATE', payload: { up: true } });
    frameCb();
    assert(c6c.state === PlayerState.PLAYING, 'non-critical RENDER throw does not transition to ERROR');
    assert(warnings.some((w) => w.msg === 'action_executor_error' && w.data && w.data.action === 'RENDER'), 'warning logged for RENDER');
  }

  // ---- Test 6d: stop() rejection is logged (P1_02), system in defined state
  {
    const stopWarnings = [];
    const logStop = {
      debug: () => {},
      info: () => {},
      warn: (msg, data) => { if (msg === 'stop_failed') stopWarnings.push(data); },
      error: () => {},
    };
    const streamingStopRejects = {
      _sink: null,
      setEventSink: function(s){ this._sink = s; },
      init: async () => {},
      listStreams: async () => ([{ id: 1 }]),
      watch: async function(){ if (this._sink) this._sink({ type: 'STREAMING_OFFER_RECEIVED', payload: {} }); },
      stop: () => Promise.reject(new Error('stop_rejected')),
      detach: async () => {},
      recreate: async () => {},
      getInboundStream: () => ({}),
      getPeerConnection: () => null,
    };
    const c6d = new AP.App.PlayerController(cfg, rtcConfig, ui, clock, logStop, streamingStopRejects, stats, null);
    await c6d.init();
    streamingStopRejects._sink({ type: 'WEBRTC_STATE', payload: { up: true } });
    frameCb();
    assert(c6d.state === PlayerState.PLAYING, 'c6d PLAYING');
    c6d.handleEvent({ type: AP.Core.EventType.STOP_REQUEST, generation: c6d._sessionToken });
    await flushMicrotasks();
    assert(c6d.state === PlayerState.IDLE, 'after stop request, state IDLE');
    assert(stopWarnings.length >= 1 && stopWarnings.some((d) => d.reason && String(d.error).includes('stop_rejected')), 'stop_failed warn logged');
  }

  // ---- Test 5b: no illegal transition — IDLE + STREAM_RECOVERED -> fail-closed ERROR (P0 TASK 1)
  {
    const EventType = AP.Core.EventType;
    const cfgNoAutoplay = Object.assign({}, cfg, { autoplayEnabled: false });
    const c5b = new AP.App.PlayerController(cfgNoAutoplay, rtcConfig, ui, clock, log, streaming, stats, null);
    await c5b.init();
    assert(c5b.state === PlayerState.IDLE, 'no autoplay -> IDLE');
    c5b.handleEvent({ type: EventType.STREAM_RECOVERED, generation: c5b._sessionToken });
    assert(c5b.state === PlayerState.ERROR, 'IDLE + STREAM_RECOVERED -> fail-closed ERROR (no illegal transition)');
  }

  // ---- Test 6: late event (stale generation) is dropped; state unchanged (E3 / C1)
  {
    const EventType = AP.Core.EventType;
    const dropped = [];
    const logCapture = {
      debug: (msg, data) => { if (msg === 'EVENT_DROPPED' && data && data.stale) dropped.push(data); },
      info: () => {},
      warn: () => {},
      error: () => {},
    };
    const c6 = new AP.App.PlayerController(cfg, rtcConfig, ui, clock, logCapture, streaming, stats, null);
    await c6.init();
    streaming._sink({ type: 'WEBRTC_STATE', payload: { up: true } });
    frameCb();
    assert(c6.state === PlayerState.PLAYING, 'c6 PLAYING');
    const tokenBefore = c6._sessionToken;
    // Event with stale generation must be dropped (generation 0 !== current token after PLAY_REQUEST).
    c6.handleEvent({ type: EventType.STOP_REQUEST, generation: 0 });
    assert(c6.state === PlayerState.PLAYING, 'late event dropped: state still PLAYING');
    assert(c6._sessionToken === tokenBefore, 'token unchanged after dropped event');
    assert(dropped.length >= 1 && dropped.some((d) => d.context === 'handleEvent'), 'EVENT_DROPPED logged for stale generation');
  }

  // ---- Test 6e: late stream event (old token) does not change state nor flags (P0 TASK 2)
  {
    const droppedStream = [];
    const logDrop = {
      debug: (msg, data) => { if (msg === 'EVENT_DROPPED' && data && data.context === 'stream_event') droppedStream.push(data); },
      info: () => {},
      warn: () => {},
      error: () => {},
    };
    const c6e = new AP.App.PlayerController(cfg, rtcConfig, ui, clock, logDrop, streaming, stats, null);
    await c6e.init();
    assert(c6e.state === PlayerState.CONNECTING, 'c6e CONNECTING');
    const tokenBefore = c6e._sessionToken;
    assert(tokenBefore >= 1, 'session token bumped after PLAY_REQUEST');
    const staleEv = { type: 'WEBRTC_STATE', payload: { up: false }, token: tokenBefore - 1 };
    streaming._sink(staleEv);
    await flushMicrotasks();
    assert(c6e.state === PlayerState.CONNECTING, 'late stream event dropped: state still CONNECTING');
    assert(c6e._sessionToken === tokenBefore, 'token unchanged');
    assert(droppedStream.length >= 1, 'EVENT_DROPPED logged for stale stream event');
  }

  // ---- Test 6f: stop then immediate connect — no exception; ends in CONNECTING or PLAYING (P0 TASK 3)
  {
    const c6f = new AP.App.PlayerController(cfg, rtcConfig, ui, clock, log, streaming, stats, null);
    await c6f.init();
    streaming._sink({ type: 'WEBRTC_STATE', payload: { up: true } });
    frameCb();
    assert(c6f.state === PlayerState.PLAYING, 'c6f PLAYING');
    c6f.togglePlay();
    c6f.togglePlay();
    await flushMicrotasks();
    await clock.advance(100);
    await flushMicrotasks();
    assert(c6f.state === PlayerState.CONNECTING || c6f.state === PlayerState.PLAYING, 'stop then immediate connect ends in CONNECTING or PLAYING');
  }

  // ---- Test 7: destroy() cleans up and makes controller inert
  {
    const c7 = new AP.App.PlayerController(cfg, rtcConfig, ui, clock, log, streaming, stats, null);
    await c7.init();
    streaming._sink({ type: 'WEBRTC_STATE', payload: { up: true } });
    frameCb();
    assert(c7.state === PlayerState.PLAYING, 'c7 PLAYING before destroy');
    c7.destroy();
    assert(c7.desiredPlaying === false, 'destroy sets desiredPlaying false');
    // After destroy, requestRecovery should be a no-op (desiredPlaying is false).
    c7.requestRecovery('no_frames', AP.Core.RecoverySeverity.SOFT);
    await clock.advance(2000);
    await flushMicrotasks();
    // State should not have changed to RECONNECTING since desiredPlaying is false.
    assert(c7.state !== PlayerState.RECONNECTING, 'destroy prevents further recovery');
  }

  // ---- Test 8: ICE_FAILED always triggers REQUEST_RECOVERY even with media flowing
  {
    const c8 = new AP.App.PlayerController(cfg, rtcConfig, ui, clock, log, streaming, stats, null);
    await c8.init();
    streaming._sink({ type: 'WEBRTC_STATE', payload: { up: true } });
    frameCb();
    assert(c8.state === PlayerState.PLAYING, 'c8 PLAYING');
    // ICE_FAILED should trigger reconnect even though media was flowing (firstFrameReceived=true).
    streaming._sink({ type: 'ICE_STATE', payload: { state: 'failed' } });
    assert(c8.state === PlayerState.RECONNECTING, 'ICE_FAILED with media flowing enters RECONNECTING');
  }

  // ---- Test 9: RECONNECTING→PLAYING resets coordinator (prevents stale exhaustion)
  {
    const c9 = new AP.App.PlayerController(cfg, rtcConfig, ui, clock, log, streaming, stats, null);
    await c9.init();
    streaming._sink({ type: 'WEBRTC_STATE', payload: { up: true } });
    frameCb();
    assert(c9.state === PlayerState.PLAYING, 'c9 PLAYING');

    // Enter RECONNECTING
    streaming._sink({ type: 'WEBRTC_STATE', payload: { up: false } });
    c9.requestRecovery('no_frames', AP.Core.RecoverySeverity.SOFT);
    assert(c9.state === PlayerState.RECONNECTING, 'c9 RECONNECTING');

    // Simulate recovery via STREAM_RECOVERED (bypassing coordinator)
    const EventType = AP.Core.EventType;
    streaming._sink({ type: 'WEBRTC_STATE', payload: { up: true } });
    c9.handleEvent({ type: EventType.STREAM_RECOVERED, generation: c9._sessionToken });
    assert(c9.state === PlayerState.PLAYING, 'c9 back to PLAYING');
    // Coordinator must be reset — attempt count should be 0
    assert(c9._reconnect.attempt() === 0, 'coordinator reset on RECONNECTING→PLAYING: attempt=0');
    assert(c9._reconnect.inFlight() === false, 'coordinator reset on RECONNECTING→PLAYING: inFlight=false');
  }

  // ---- Test 10: failClosed includes RENDER action (UI always updates on ERROR)
  {
    let renderCount = 0;
    const uiRenderCount = {
      startFrameClock: (cb) => { frameCb = cb; },
      bindIntents: () => {},
      render: () => { renderCount++; },
      bindStream: () => {},
      ensurePlaying: async () => ({ ok: true, blocked: false }),
    };
    const c10 = new AP.App.PlayerController(cfg, rtcConfig, uiRenderCount, clock, log, streaming, stats, null);
    await c10.init();
    streaming._sink({ type: 'WEBRTC_STATE', payload: { up: true } });
    frameCb();
    assert(c10.state === PlayerState.PLAYING, 'c10 PLAYING');
    renderCount = 0;
    c10.handleEvent({ type: 'INVALID_EVENT' });
    assert(c10.state === PlayerState.ERROR, 'c10 ERROR after invalid event');
    assert(renderCount >= 1, 'failClosed RENDER: UI updated on ERROR transition');
  }

  // ---- Test 11: Stop→Play race — _connectInFlight latch cleared so second Play proceeds
  {
    const clock11 = createFakeClock();
    let frameCb11 = null;

    const ui11 = {
      startFrameClock: (cb) => { frameCb11 = cb; },
      bindIntents: () => {},
      render: () => {},
      bindStream: () => {},
      ensurePlaying: async () => ({ ok: true, blocked: false }),
    };

    let initResolve11 = null;
    let watchCalls11 = 0;
    const streaming11 = {
      _sink: null,
      setEventSink: function(s){ this._sink = s; },
      init: () => new Promise((resolve) => { initResolve11 = resolve; }),
      listStreams: async () => ([{ id: 1 }]),
      watch: async function(){ watchCalls11++; if (this._sink) this._sink({ type: 'STREAMING_OFFER_RECEIVED', payload: {} }); },
      stop: async () => {},
      detach: async () => {},
      recreate: async () => {},
      getInboundStream: () => ({}),
      getPeerConnection: () => null,
    };

    const c11 = new AP.App.PlayerController(cfg, rtcConfig, ui11, clock11, log, streaming11, stats, null);
    const initP11 = c11.init();
    await flushMicrotasks();

    assert(c11.state === PlayerState.CONNECTING, 'c11 autoplay -> CONNECTING');
    assert(c11._connectInFlight === true, 'connect latch set during init');

    // Stop while init is still pending
    c11.togglePlay();
    assert(c11.state === PlayerState.IDLE, 'c11 Stop -> IDLE');
    assert(c11._connectInFlight === false, 'Stop clears _connectInFlight via _stopAll');

    // Play again — must NOT be blocked by stale latch
    c11.togglePlay();
    assert(c11.state === PlayerState.CONNECTING, 'c11 second Play -> CONNECTING');
    assert(c11._connectInFlight === true, 'new connect flow sets latch');

    // Resolve the old init (stale flow should be dropped)
    initResolve11(true);
    await initP11;
    await flushMicrotasks();

    // The second flow's init is still pending (different promise),
    // but the stale first flow should NOT have called watch.
    assert(watchCalls11 === 0, 'stale first flow dropped, no watch call');
    assert(c11.state === PlayerState.CONNECTING, 'c11 still CONNECTING (second flow pending)');
  }

  console.log('OK: controller tests passed');
}

if (require.main === module) {
  main().catch((e) => {
    console.error('FAILED:', e);
    process.exitCode = 1;
  });
}
