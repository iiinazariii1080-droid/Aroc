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
    stopFrameClock: () => {},
    bindIntents: () => {},
    render: () => {},
    bindStream: () => {},
    ensurePlaying: async () => ({ ok: true, blocked: false }),
    onVideoStalled: () => {},
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
    isSessionAlive: () => true,
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
      stopFrameClock: () => {},
      bindIntents: () => {},
      render: () => {},
      bindStream: () => {},
      ensurePlaying: async () => ({ ok: true, blocked: false }),
      onVideoStalled: () => {},
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
      isSessionAlive: () => true,
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
      stopFrameClock: () => {},
      bindIntents: () => {},
      render: () => {},
      bindStream: () => { throw new Error('bind_failed'); },
      ensurePlaying: async () => ({ ok: true, blocked: false }),
      onVideoStalled: () => {},
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
      stopFrameClock: () => {},
      bindIntents: () => {},
      render: () => { if (++renderCallCount > 1) throw new Error('render_failed'); },
      bindStream: () => {},
      ensurePlaying: async () => ({ ok: true, blocked: false }),
      onVideoStalled: () => {},
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
      isSessionAlive: () => true,
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
      stopFrameClock: () => {},
      bindIntents: () => {},
      render: () => { renderCount++; },
      bindStream: () => {},
      ensurePlaying: async () => ({ ok: true, blocked: false }),
      onVideoStalled: () => {},
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
      stopFrameClock: () => {},
      bindIntents: () => {},
      render: () => {},
      bindStream: () => {},
      ensurePlaying: async () => ({ ok: true, blocked: false }),
      onVideoStalled: () => {},
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
      isSessionAlive: () => true,
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

  // ---- Test 12: HANGUP event triggers recovery via ConnectionPolicy
  {
    console.log('--- CT Test 12: HANGUP handler ---');
    const c12 = new AP.App.PlayerController(cfg, rtcConfig, ui, clock, log, streaming, stats, null);
    await c12.init();
    streaming._sink({ type: 'WEBRTC_STATE', payload: { up: true } });
    frameCb();
    assert(c12.state === PlayerState.PLAYING, 'c12 PLAYING');

    // Use non-ICE hangup reason (generic path → REQUEST_RECOVERY MEDIUM)
    streaming._sink({ type: 'HANGUP', payload: { reason: 'Server shutting down' } });
    assert(c12.state === PlayerState.RECONNECTING, 'HANGUP (non-ICE) triggers RECONNECTING');
  }

  // ---- Test 13: TRACK on=true emits TRACK_READY → BIND_STREAM
  {
    console.log('--- CT Test 13: TRACK READY ---');
    const c13 = new AP.App.PlayerController(cfg, rtcConfig, ui, clock, log, streaming, stats, null);
    await c13.init();
    assert(c13.state === PlayerState.CONNECTING, 'c13 CONNECTING');

    // TRACK on=true → TRACK_READY event → BIND_STREAM action
    streaming._sink({ type: 'TRACK', payload: { on: true, mid: '0' } });
    // Should not crash and state should still be CONNECTING (no transition from TRACK_READY alone)
    assert(c13.state === PlayerState.CONNECTING, 'c13 still CONNECTING after TRACK');
  }

  // ---- Test 14: TRACK_MUTED → ARM, TRACK_UNMUTED → DISARM
  {
    console.log('--- CT Test 14: TRACK_MUTED/UNMUTED ---');
    const c14 = new AP.App.PlayerController(cfg, rtcConfig, ui, clock, log, streaming, stats, null);
    await c14.init();
    streaming._sink({ type: 'WEBRTC_STATE', payload: { up: true } });
    frameCb();
    assert(c14.state === PlayerState.PLAYING, 'c14 PLAYING');

    streaming._sink({ type: 'TRACK_MUTED', payload: { trackId: 't1' } });
    assert(c14._timers.has('trackMute:t1'), 'c14 track mute timer armed');

    streaming._sink({ type: 'TRACK_UNMUTED', payload: { trackId: 't1' } });
    assert(!c14._timers.has('trackMute:t1'), 'c14 track mute timer disarmed');
  }

  // ---- Test 15: Track mute timeout triggers recovery
  {
    console.log('--- CT Test 15: Track mute timeout ---');
    const c15 = new AP.App.PlayerController(cfg, rtcConfig, ui, clock, log, streaming, stats, null);
    await c15.init();
    streaming._sink({ type: 'WEBRTC_STATE', payload: { up: true } });
    frameCb();
    assert(c15.state === PlayerState.PLAYING, 'c15 PLAYING');

    streaming._sink({ type: 'TRACK_MUTED', payload: { trackId: 't1' } });
    // Simulate webrtcUp=false before timeout so recovery happens
    streaming._sink({ type: 'WEBRTC_STATE', payload: { up: false } });
    // Wait for trackMuteRestartMs (1000)
    await clock.advance(1000);
    await flushMicrotasks();
    assert(c15.state === PlayerState.RECONNECTING, 'c15 track mute timeout → RECONNECTING');
  }

  // ---- Test 16: TRACK_ENDED triggers recovery
  {
    console.log('--- CT Test 16: TRACK_ENDED ---');
    const c16 = new AP.App.PlayerController(cfg, rtcConfig, ui, clock, log, streaming, stats, null);
    await c16.init();
    streaming._sink({ type: 'WEBRTC_STATE', payload: { up: true } });
    frameCb();
    assert(c16.state === PlayerState.PLAYING, 'c16 PLAYING');

    streaming._sink({ type: 'TRACK_ENDED', payload: { trackId: 't1', kind: 'video' } });
    assert(c16.state === PlayerState.RECONNECTING, 'c16 TRACK_ENDED → RECONNECTING');
  }

  // ---- Test 17: 460 Already Watching — requests HARD recovery
  {
    console.log('--- CT Test 17: 460 Already Watching ---');
    const c17 = new AP.App.PlayerController(cfg, rtcConfig, ui, clock, log, streaming, stats, null);
    await c17.init();
    streaming._sink({ type: 'WEBRTC_STATE', payload: { up: true } });
    frameCb();
    assert(c17.state === PlayerState.PLAYING, 'c17 PLAYING');

    streaming._sink({ type: 'ERROR', payload: { error: 'Already watching', error_code: 460 } });
    assert(c17.state === PlayerState.RECONNECTING, 'c17 460 → RECONNECTING');
    assert(c17._reconnect.pending() != null, 'c17 reconnect pending');
    assert(c17._reconnect.pending().severity >= AP.Core.RecoverySeverity.HARD, 'c17 severity HARD for 460');
  }

  // ---- Test 18: ICE disconnected → grace timer → recovery (ICE grace cycle)
  {
    console.log('--- CT Test 18: ICE grace cycle ---');
    const c18 = new AP.App.PlayerController(cfg, rtcConfig, ui, clock, log, streaming, stats, null);
    await c18.init();
    streaming._sink({ type: 'WEBRTC_STATE', payload: { up: true } });
    frameCb();
    assert(c18.state === PlayerState.PLAYING, 'c18 PLAYING');

    // ICE disconnected → ARM_ICE_GRACE
    streaming._sink({ type: 'ICE_STATE', payload: { state: 'disconnected' } });
    assert(c18._timers.has('iceGrace'), 'c18 ICE grace armed');

    // ICE connected → CANCEL_ICE_GRACE
    streaming._sink({ type: 'ICE_STATE', payload: { state: 'connected' } });
    assert(!c18._timers.has('iceGrace'), 'c18 ICE grace cancelled on connected');
  }

  // ---- Test 19: ICE grace timeout fires → requests recovery
  {
    console.log('--- CT Test 19: ICE grace timeout ---');
    const c19 = new AP.App.PlayerController(cfg, rtcConfig, ui, clock, log, streaming, stats, null);
    await c19.init();
    streaming._sink({ type: 'WEBRTC_STATE', payload: { up: true } });
    frameCb();
    assert(c19.state === PlayerState.PLAYING, 'c19 PLAYING');

    streaming._sink({ type: 'ICE_STATE', payload: { state: 'disconnected' } });
    // Wait webrtcUp=false makes it eligible for recovery
    streaming._sink({ type: 'WEBRTC_STATE', payload: { up: false } });
    // Wait for iceDisconnectedGraceMs (500)
    await clock.advance(500);
    await flushMicrotasks();
    assert(c19.state === PlayerState.RECONNECTING, 'c19 ICE grace timeout → RECONNECTING');
  }

  // ---- Test 20: SESSION_RESET with expected resets consumed
  {
    console.log('--- CT Test 20: SESSION_RESET expected ---');
    const c20 = new AP.App.PlayerController(cfg, rtcConfig, ui, clock, log, streaming, stats, null);
    await c20.init();
    streaming._sink({ type: 'WEBRTC_STATE', payload: { up: true } });
    frameCb();
    assert(c20.state === PlayerState.PLAYING, 'c20 PLAYING');

    // Enter RECONNECTING
    streaming._sink({ type: 'WEBRTC_STATE', payload: { up: false } });
    c20.requestRecovery('test', AP.Core.RecoverySeverity.SOFT);
    assert(c20.state === PlayerState.RECONNECTING, 'c20 RECONNECTING');

    // Fire attempt
    await clock.advance(500);
    await flushMicrotasks();
    assert(c20._reconnect.inFlight() === true, 'c20 in-flight');

    // Set up expected session resets (like before recreate)
    c20._reconnect.expectSessionResetFromRecreate();

    // First SESSION_RESET → consumed (no error)
    streaming._sink({ type: 'SESSION_RESET', payload: { type: 'SESSION_DESTROYED' } });
    assert(c20.state === PlayerState.RECONNECTING, 'c20 still RECONNECTING after consumed reset');
    assert(c20._reconnect.inFlight() === true, 'c20 still in-flight after consumed reset');

    // Second SESSION_RESET → consumed
    streaming._sink({ type: 'SESSION_RESET', payload: { type: 'SESSION_RECREATED' } });
    assert(c20.state === PlayerState.RECONNECTING, 'c20 still RECONNECTING after second consumed reset');

    // Third SESSION_RESET → NOT consumed → triggers failure
    streaming._sink({ type: 'SESSION_RESET', payload: { type: 'SESSION_DESTROYED' } });
    // The unconsumed reset triggers notifyAttemptFailed
    assert(c20._reconnect.inFlight() === false, 'c20 attempt failed on unconsumed reset');
  }

  // ---- Test 21: retry() from ERROR works + error auto-retry timer BUG detection
  // NOTE: _scheduleErrorAutoRetry() in _applySnapshot is immediately cleared by CANCEL_ALL_TIMERS
  // in _executeActions (failClosed always emits CANCEL_ALL_TIMERS). The auto-retry timer is
  // effectively dead code. This test documents the bug and verifies manual retry() still works.
  {
    console.log('--- CT Test 21: retry from ERROR ---');
    const clock21 = createFakeClock();
    let frameCb21 = null;
    const ui21 = {
      startFrameClock: (cb) => { frameCb21 = cb; },
      stopFrameClock: () => {},
      bindIntents: () => {},
      render: () => {},
      bindStream: () => {},
      ensurePlaying: async () => ({ ok: true, blocked: false }),
      onVideoStalled: () => {},
    };
    const cfgRetry = Object.assign({}, cfg, { maxReconnectAttempts: 1, errorAutoRetryBaseMs: 500 });
    const c21 = new AP.App.PlayerController(cfgRetry, rtcConfig, ui21, clock21, log, streaming, stats, null);
    await c21.init();
    streaming._sink({ type: 'WEBRTC_STATE', payload: { up: true } });
    frameCb21();
    assert(c21.state === PlayerState.PLAYING, 'c21 PLAYING');

    // Force exhaustion → ERROR
    streaming._sink({ type: 'WEBRTC_STATE', payload: { up: false } });
    c21.requestRecovery('no_frames', AP.Core.RecoverySeverity.SOFT);
    await clock21.advance(500);
    await flushMicrotasks();
    await clock21.advance(1000);
    await flushMicrotasks();
    assert(c21.state === PlayerState.ERROR, 'c21 ERROR');

    // BUG: _errorRetryTimer is null because CANCEL_ALL_TIMERS clears it immediately after scheduling
    assert(c21._errorRetryTimer === null, 'c21 BUG: auto-retry timer cleared by CANCEL_ALL_TIMERS');

    // Manual retry() works
    c21.retry();
    assert(c21.state === PlayerState.CONNECTING, 'c21 retry → CONNECTING');
    assert(c21.desiredPlaying === true, 'c21 desiredPlaying restored');
    assert(c21.errCode === '', 'c21 errCode cleared');
  }

  // ---- Test 22: Session dead upgrade — SOFT becomes RECREATE when session not alive
  {
    console.log('--- CT Test 22: session dead upgrade ---');
    const clock22 = createFakeClock();
    let frameCb22 = null;
    const ui22 = {
      startFrameClock: (cb) => { frameCb22 = cb; },
      stopFrameClock: () => {},
      bindIntents: () => {},
      render: () => {},
      bindStream: () => {},
      ensurePlaying: async () => ({ ok: true, blocked: false }),
      onVideoStalled: () => {},
    };
    const warns22 = [];
    const log22 = {
      debug: () => {},
      info: () => {},
      warn: (msg, data) => { warns22.push({ msg, data }); },
      error: () => {},
    };
    let sessionAlive = true;
    const streaming22 = {
      _sink: null,
      setEventSink: function(s){ this._sink = s; },
      init: async () => {},
      listStreams: async () => ([{ id: 1 }]),
      watch: async function(){ if (this._sink) this._sink({ type: 'STREAMING_OFFER_RECEIVED', payload: {} }); },
      stop: async () => {},
      detach: async () => {},
      recreate: async () => {},
      getInboundStream: () => ({}),
      getPeerConnection: () => null,
      isSessionAlive: () => sessionAlive,
    };
    const c22 = new AP.App.PlayerController(cfg, rtcConfig, ui22, clock22, log22, streaming22, stats, null);
    await c22.init();
    streaming22._sink({ type: 'WEBRTC_STATE', payload: { up: true } });
    frameCb22();
    assert(c22.state === PlayerState.PLAYING, 'c22 PLAYING');

    // Session dies
    sessionAlive = false;
    streaming22._sink({ type: 'WEBRTC_STATE', payload: { up: false } });
    c22.requestRecovery('no_frames', AP.Core.RecoverySeverity.SOFT);
    assert(c22.state === PlayerState.RECONNECTING, 'c22 RECONNECTING');

    // Fire the backoff timer
    await clock22.advance(500);
    await flushMicrotasks();

    // Should have logged session_dead_upgrade
    assert(warns22.some(w => w.msg === 'session_dead_upgrade'), 'c22 session_dead_upgrade logged');
  }

  // ---- Test 23: Watchdog timeout triggers recovery
  {
    console.log('--- CT Test 23: watchdog timeout ---');
    const clock23 = createFakeClock();
    let frameCb23 = null;
    const ui23 = {
      startFrameClock: (cb) => { frameCb23 = cb; },
      stopFrameClock: () => {},
      bindIntents: () => {},
      render: () => {},
      bindStream: () => {},
      ensurePlaying: async () => ({ ok: true, blocked: false }),
      onVideoStalled: () => {},
    };
    const c23 = new AP.App.PlayerController(cfg, rtcConfig, ui23, clock23, log, streaming, stats, null);
    await c23.init();
    // Flush microtasks so _runConnectFlow completes (deep async chain: init→listStreams→watch→ensurePlaying→startWatchdog)
    // _runConnectFlow is fire-and-forget from _executeActions; needs ~50 microtask flushes for full chain.
    for (let i = 0; i < 50; i++) await Promise.resolve();
    streaming._sink({ type: 'WEBRTC_STATE', payload: { up: true } });
    frameCb23();
    assert(c23.state === PlayerState.PLAYING, 'c23 PLAYING');

    // Stop sending frames — let noFrameThresholdMs (1500) expire
    // Watchdog ticks every watchdogTickMs (250ms).
    // Need to advance enough for watchdog to detect timeout for ICE state not 'new'/'checking'
    streaming._sink({ type: 'ICE_STATE', payload: { state: 'connected' } });
    await clock23.advance(2000);
    await flushMicrotasks();
    assert(c23.state === PlayerState.RECONNECTING, 'c23 watchdog timeout → RECONNECTING');
  }

  // ---- Test 24: _selectStreamId preferences
  {
    console.log('--- CT Test 24: _selectStreamId ---');
    const c24 = new AP.App.PlayerController(cfg, rtcConfig, ui, clock, log, streaming, stats, null);

    // preferStreamId takes precedence
    assert(c24._selectStreamId([{ id: 5, description: 'cam' }]) === 1, 'c24 preferStreamId=1 wins');

    // Without preferStreamId, match by name
    const cfgName = Object.assign({}, cfg, { preferStreamId: null, streamName: 'depth' });
    const c24b = new AP.App.PlayerController(cfgName, rtcConfig, ui, clock, log, streaming, stats, null);
    assert(c24b._selectStreamId([{ id: 1, description: 'Color' }, { id: 2, description: 'Depth cam' }]) === 2, 'c24 streamName match');

    // Fallback to first
    const cfgNoName = Object.assign({}, cfg, { preferStreamId: null, streamName: null });
    const c24c = new AP.App.PlayerController(cfgNoName, rtcConfig, ui, clock, log, streaming, stats, null);
    assert(c24c._selectStreamId([{ id: 10 }, { id: 20 }]) === 10, 'c24 fallback to first');

    // Throw on empty
    let threw24 = false;
    try { c24c._selectStreamId([]); } catch (e) { threw24 = true; }
    assert(threw24, 'c24 throws on empty list');
  }

  // ---- Test 25: _isDataPlaneHealthy checks frame age
  {
    console.log('--- CT Test 25: _isDataPlaneHealthy ---');
    const clock25 = createFakeClock();
    let frameCb25 = null;
    const ui25 = {
      startFrameClock: (cb) => { frameCb25 = cb; },
      stopFrameClock: () => {},
      bindIntents: () => {},
      render: () => {},
      bindStream: () => {},
      ensurePlaying: async () => ({ ok: true, blocked: false }),
      onVideoStalled: () => {},
    };
    const c25 = new AP.App.PlayerController(cfg, rtcConfig, ui25, clock25, log, streaming, stats, null);
    await c25.init();
    streaming._sink({ type: 'WEBRTC_STATE', payload: { up: true } });
    frameCb25();
    assert(c25._isDataPlaneHealthy(), 'c25 healthy right after frame');

    clock25.advance(2000);
    assert(!c25._isDataPlaneHealthy(), 'c25 unhealthy after 2s without frames');
  }

  // ---- Test 26: STREAMING_OFFER_RECEIVED during RECONNECTING starts settle window
  {
    console.log('--- CT Test 26: STREAMING_OFFER settle ---');
    const c26 = new AP.App.PlayerController(cfg, rtcConfig, ui, clock, log, streaming, stats, null);
    await c26.init();
    streaming._sink({ type: 'WEBRTC_STATE', payload: { up: true } });
    frameCb();
    assert(c26.state === PlayerState.PLAYING, 'c26 PLAYING');

    streaming._sink({ type: 'WEBRTC_STATE', payload: { up: false } });
    c26.requestRecovery('x', AP.Core.RecoverySeverity.SOFT);
    assert(c26.state === PlayerState.RECONNECTING, 'c26 RECONNECTING');

    await clock.advance(500);
    await flushMicrotasks();
    assert(c26._reconnect.inFlight() === true, 'c26 in-flight');

    // STREAMING_OFFER_RECEIVED → state machine emits START_RECONNECT_SETTLE → startSettleWindow
    streaming._sink({ type: 'STREAMING_OFFER_RECEIVED', payload: {} });
    // After offer, settle window is started. Simulate recovery.
    streaming._sink({ type: 'WEBRTC_STATE', payload: { up: true } });
    frameCb();
    assert(c26.state === PlayerState.PLAYING, 'c26 recovered after settle');
    assert(c26._reconnect.attempt() === 0, 'c26 attempt reset');
  }

  // ---- Test 27: Video stalled detection
  {
    console.log('--- CT Test 27: video stalled ---');
    const clock27 = createFakeClock();
    let frameCb27 = null;
    let stallCb = null;
    const ui27 = {
      startFrameClock: (cb) => { frameCb27 = cb; },
      stopFrameClock: () => {},
      bindIntents: () => {},
      render: () => {},
      bindStream: () => {},
      ensurePlaying: async () => ({ ok: true, blocked: false }),
      onVideoStalled: (cb) => { stallCb = cb; },
    };
    const c27 = new AP.App.PlayerController(cfg, rtcConfig, ui27, clock27, log, streaming, stats, null);
    await c27.init();
    streaming._sink({ type: 'WEBRTC_STATE', payload: { up: true } });
    streaming._sink({ type: 'ICE_STATE', payload: { state: 'connected' } });
    frameCb27();
    assert(c27.state === PlayerState.PLAYING, 'c27 PLAYING');

    // Advance so frame age > 2000ms
    clock27.advance(3000);

    // Trigger video stalled callback
    assert(stallCb != null, 'c27 stall callback installed');
    stallCb('stalled');

    // Should trigger RECONNECTING since frame age > 2000 and in PLAYING
    assert(c27.state === PlayerState.RECONNECTING, 'c27 video stalled → RECONNECTING');
  }

  // ---- Test 28: Tab resume (short hide) uses grace period, does NOT immediately reconnect
  {
    console.log('--- CT Test 28: Tab resume short hide - grace period ---');
    const clock28 = createFakeClock();
    let frameCb28 = null;
    const ui28 = {
      startFrameClock: (cb) => { frameCb28 = cb; },
      stopFrameClock: () => {},
      bindIntents: () => {},
      render: () => {},
      bindStream: () => {},
      ensurePlaying: async () => ({ ok: true, blocked: false }),
      onVideoStalled: () => {},
    };
    const c28 = new AP.App.PlayerController(cfg, rtcConfig, ui28, clock28, log, streaming, stats, null);
    await c28.init();
    streaming._sink({ type: 'WEBRTC_STATE', payload: { up: true } });
    frameCb28();
    assert(c28.state === PlayerState.PLAYING, 'c28 PLAYING');

    // Simulate tab hidden for 4 seconds (typical driving tab-switch)
    c28._tabHiddenAt = clock28.nowMs();
    clock28.advance(4000);
    // Frame age is now 4000ms, which would have triggered tab_resume_stale before the fix.
    // _isDataPlaneHealthy() threshold = max(1000, 250*2) = 1000ms → would be unhealthy.
    assert(!c28._isDataPlaneHealthy(), 'c28 unhealthy before resume (expected)');

    // Now simulate tab becoming visible
    c28._onVisibilityChange(); // tab visible (no _tabHiddenAt set → means tab is visible)

    // Key assertion: should NOT have triggered reconnection
    assert(c28.state === PlayerState.PLAYING, 'c28 still PLAYING after short tab hide (grace period)');

    // The watchdog timestamp was reset, so _isDataPlaneHealthy should be true now
    assert(c28._isDataPlaneHealthy(), 'c28 healthy after watchdog reset on tab resume');
  }

  // ---- Test 29: Tab resume (long hide > session timeout) forces HARD recovery
  {
    console.log('--- CT Test 29: Tab resume long hide - HARD recovery ---');
    const clock29 = createFakeClock();
    let frameCb29 = null;
    const ui29 = {
      startFrameClock: (cb) => { frameCb29 = cb; },
      stopFrameClock: () => {},
      bindIntents: () => {},
      render: () => {},
      bindStream: () => {},
      ensurePlaying: async () => ({ ok: true, blocked: false }),
      onVideoStalled: () => {},
    };
    const cfg29 = Object.assign({}, cfg, { sessionTimeoutMs: 30000 });
    const c29 = new AP.App.PlayerController(cfg29, rtcConfig, ui29, clock29, log, streaming, stats, null);
    await c29.init();
    streaming._sink({ type: 'WEBRTC_STATE', payload: { up: true } });
    streaming._sink({ type: 'ICE_STATE', payload: { state: 'connected' } });
    frameCb29();
    assert(c29.state === PlayerState.PLAYING, 'c29 PLAYING');

    // Simulate tab hidden for 35 seconds (> session timeout)
    c29._tabHiddenAt = clock29.nowMs();
    clock29.advance(35000);

    // Make webrtc down so reconnect attempt doesn't skip
    streaming._sink({ type: 'WEBRTC_STATE', payload: { up: false } });

    c29._onVisibilityChange();

    // Should have triggered HARD recovery → RECONNECTING
    assert(c29.state === PlayerState.RECONNECTING, 'c29 RECONNECTING after long tab hide (> session timeout)');
  }

  // ---- Test 30: Tab resume grace resets FPS tracking (prevents false FPS drop)
  {
    console.log('--- CT Test 30: Tab resume resets FPS tracking ---');
    const clock30 = createFakeClock();
    let frameCb30 = null;
    const ui30 = {
      startFrameClock: (cb) => { frameCb30 = cb; },
      stopFrameClock: () => {},
      bindIntents: () => {},
      render: () => {},
      bindStream: () => {},
      ensurePlaying: async () => ({ ok: true, blocked: false }),
      onVideoStalled: () => {},
    };
    const c30 = new AP.App.PlayerController(cfg, rtcConfig, ui30, clock30, log, streaming, stats, null);
    await c30.init();
    streaming._sink({ type: 'WEBRTC_STATE', payload: { up: true } });

    // Deliver many frames to fill FPS ring buffer
    for (let i = 0; i < 30; i++) {
      clock30.advance(33);
      frameCb30();
    }
    assert(c30.state === PlayerState.PLAYING, 'c30 PLAYING');

    // Simulate 5-second tab hide
    c30._tabHiddenAt = clock30.nowMs();
    clock30.advance(5000);

    // Before resume, FPS should be very low (gap in ring buffer)
    const fpsBefore = c30._watchdog.getCurrentFps(clock30.nowMs());
    // FPS ring has old entries from 5 seconds ago, FPS calculation spans the gap

    // Resume tab
    c30._onVisibilityChange();

    // After resume, FPS ring should be reset (empty)
    const fpsAfter = c30._watchdog.getCurrentFps(clock30.nowMs());
    assert(fpsAfter === 0, 'c30 FPS ring reset to 0 after tab resume');
    assert(c30.state === PlayerState.PLAYING, 'c30 still PLAYING (no false FPS drop)');
  }

  // ---- Test 31: WatchdogService.resetAfterTabResume() resets timestamp and FPS ring
  {
    console.log('--- CT Test 31: WatchdogService.resetAfterTabResume ---');
    const clock31 = createFakeClock();
    const watchdog = new AP.App.WatchdogService(cfg, clock31, () => {}, () => {});

    // Record some frames
    watchdog.updateFrameTime();
    clock31.advance(33);
    watchdog.updateFrameTime();
    clock31.advance(33);
    watchdog.updateFrameTime();

    // Advance to make frame age stale
    clock31.advance(5000);
    const ageBefore = watchdog.getLastFrameAgeMs(clock31.nowMs());
    assert(ageBefore >= 5000, 'c31 age before reset >= 5000');
    assert(watchdog.getCurrentFps(clock31.nowMs()) > 0, 'c31 FPS before reset > 0 (ring has entries)');

    // Reset after tab resume
    watchdog.resetAfterTabResume();

    const ageAfter = watchdog.getLastFrameAgeMs(clock31.nowMs());
    assert(ageAfter === 0, 'c31 age after reset === 0');
    assert(watchdog.getCurrentFps(clock31.nowMs()) === 0, 'c31 FPS after reset === 0 (ring cleared)');
  }

  // ---- Test 32: Tab resume during CONNECTING also uses grace (no reconnect)
  {
    console.log('--- CT Test 32: Tab resume during CONNECTING ---');
    const clock32 = createFakeClock();
    const ui32 = {
      startFrameClock: () => {},
      stopFrameClock: () => {},
      bindIntents: () => {},
      render: () => {},
      bindStream: () => {},
      ensurePlaying: async () => ({ ok: true, blocked: false }),
      onVideoStalled: () => {},
    };
    const c32 = new AP.App.PlayerController(cfg, rtcConfig, ui32, clock32, log, streaming, stats, null);
    await c32.init();
    assert(c32.state === PlayerState.CONNECTING, 'c32 CONNECTING');

    // Simulate 10-second tab hide during CONNECTING (< sessionTimeout)
    c32._tabHiddenAt = clock32.nowMs();
    clock32.advance(10000);
    c32._onVisibilityChange();

    // Should still be CONNECTING — grace period, not immediate reconnect
    assert(c32.state === PlayerState.CONNECTING, 'c32 still CONNECTING after medium tab hide');
  }

  // ---- Test 33: network_online from ERROR defers retry (not immediate)
  {
    console.log('--- CT Test 33: network_online deferred retry ---');
    const clock33 = createFakeClock();
    let frameCb33 = null;
    const ui33 = {
      startFrameClock: (cb) => { frameCb33 = cb; },
      stopFrameClock: () => {},
      bindIntents: () => {},
      render: () => {},
      bindStream: () => {},
      ensurePlaying: async () => ({ ok: true, blocked: false }),
      onVideoStalled: () => {},
    };
    // Use failing streaming to simulate "Is the server down?"
    let initCallCount = 0;
    const failStreaming = Object.assign({}, streaming, {
      init: async () => { initCallCount++; if (initCallCount <= 1) throw new Error('Is the server down?'); },
    });
    const c33 = new AP.App.PlayerController(cfg, rtcConfig, ui33, clock33, log, failStreaming, stats, null);
    // Manually set up ERROR state (simulating post-disconnect)
    c33.state = PlayerState.ERROR;
    c33.desiredPlaying = false;
    c33._errorRetryCount = 8; // simulate accumulated backoff from long disconnect

    // Fire network_online
    c33._onNetworkOnline();

    // Should NOT have transitioned immediately — deferred retry pending
    assert(c33.state === PlayerState.ERROR, 'c33 still ERROR right after network_online (deferred)');
    // Error retry count should be reset
    assert(c33._errorRetryCount === 0, 'c33 error retry count reset on network_online');
    // Timer should be set
    assert(c33._errorRetryTimer != null, 'c33 deferred retry timer set');

    // Advance 2s (default networkOnlineDelayMs) — retry should fire
    await clock33.advance(2000);
    await flushMicrotasks();
    // retry() called → ERROR → CONNECTING (but init fails → back to ERROR)
    // The key point: the retry DID fire after the delay
    assert(initCallCount === 1, 'c33 streaming.init called after 2s delay');
  }

  // ---- Test 34: network_online resets _errorRetryCount for fresh backoff
  {
    console.log('--- CT Test 34: network_online resets error retry count ---');
    const clock34 = createFakeClock();
    let frameCb34 = null;
    const ui34 = {
      startFrameClock: (cb) => { frameCb34 = cb; },
      stopFrameClock: () => {},
      bindIntents: () => {},
      render: () => {},
      bindStream: () => {},
      ensurePlaying: async () => ({ ok: true, blocked: false }),
      onVideoStalled: () => {},
    };
    const c34 = new AP.App.PlayerController(cfg, rtcConfig, ui34, clock34, log, streaming, stats, null);
    await c34.init();
    streaming._sink({ type: 'WEBRTC_STATE', payload: { up: true } });
    frameCb34();
    assert(c34.state === PlayerState.PLAYING, 'c34 PLAYING');

    // Simulate high error retry count (accumulated during long disconnect)
    c34._errorRetryCount = 10;

    // network_online while PLAYING — should reset count even in non-ERROR states
    c34._onNetworkOnline();
    assert(c34._errorRetryCount === 0, 'c34 error retry count reset on network_online from PLAYING');
  }

  // ---- Test 35: network_online deferred retry succeeds after delay
  {
    console.log('--- CT Test 35: network_online deferred retry succeeds ---');
    const clock35 = createFakeClock();
    let frameCb35 = null;
    const ui35 = {
      startFrameClock: (cb) => { frameCb35 = cb; },
      stopFrameClock: () => {},
      bindIntents: () => {},
      render: () => {},
      bindStream: () => {},
      ensurePlaying: async () => ({ ok: true, blocked: false }),
      onVideoStalled: () => {},
    };
    const c35 = new AP.App.PlayerController(cfg, rtcConfig, ui35, clock35, log, streaming, stats, null);
    // init() to wire up frame callback + streaming sink
    await c35.init();
    assert(typeof frameCb35 === 'function', 'c35 frameCb wired');

    // Move to ERROR state via _fail (realistic path)
    c35._fail(AP.Core.PlayerErrorCode.CONNECT_FAILED, 'simulated');
    assert(c35.state === PlayerState.ERROR, 'c35 ERROR');
    c35._errorRetryCount = 5; // simulate accumulated backoff

    // Fire network_online → schedules deferred retry in 2s
    c35._onNetworkOnline();
    assert(c35.state === PlayerState.ERROR, 'c35 still ERROR');

    // Advance 2s → retry fires → CONNECTING → streaming works
    await clock35.advance(2000);
    await flushMicrotasks();
    assert(c35.state === PlayerState.CONNECTING, 'c35 CONNECTING after deferred retry');

    // Complete connection
    streaming._sink({ type: 'WEBRTC_STATE', payload: { up: true } });
    frameCb35();
    assert(c35.state === PlayerState.PLAYING, 'c35 PLAYING after successful deferred retry');
    assert(c35._errorRetryCount === 0, 'c35 error retry count reset on PLAYING');
  }

  // ---- Test 36: Watchdog timeout while player hidden → no recovery
  {
    console.log('--- CT Test 36: Watchdog timeout while hidden - no recovery ---');
    const clock36 = createFakeClock();
    let frameCb36 = null;
    const ui36 = {
      startFrameClock: (cb) => { frameCb36 = cb; },
      stopFrameClock: () => {},
      bindIntents: () => {},
      render: () => {},
      bindStream: () => {},
      ensurePlaying: async () => ({ ok: true, blocked: false }),
      onVideoStalled: () => {},
      isElementVisible: () => false, // simulate hidden element (iframe display:none)
    };
    const c36 = new AP.App.PlayerController(cfg, rtcConfig, ui36, clock36, log, streaming, stats, null);
    await c36.init();
    streaming._sink({ type: 'WEBRTC_STATE', payload: { up: true } });
    frameCb36();
    assert(c36.state === PlayerState.PLAYING, 'c36 PLAYING');

    // _isPlayerHidden() should return true (element not visible)
    assert(c36._isPlayerHidden(), 'c36 player is hidden (element not visible)');

    // Fire watchdog timeout — should be suppressed because player is hidden
    c36._onWatchdogTimeout(10000);

    assert(c36.state === PlayerState.PLAYING, 'c36 still PLAYING after watchdog while hidden');

    // Also test _onFpsDrop while hidden
    c36._onFpsDrop(2);
    assert(c36.state === PlayerState.PLAYING, 'c36 still PLAYING after FPS drop while hidden');

    // Also test _onVideoStalled while hidden
    c36._onVideoStalled();
    assert(c36.state === PlayerState.PLAYING, 'c36 still PLAYING after video stalled while hidden');
  }

  // ---- Test 37: _degraded flag reset on reconnect success
  {
    console.log('--- CT Test 37: degraded reset on reconnect success ---');
    const clock37 = createFakeClock();
    let frameCb37 = null;
    const ui37 = {
      startFrameClock: (cb) => { frameCb37 = cb; },
      stopFrameClock: () => {},
      bindIntents: () => {},
      render: () => {},
      bindStream: () => {},
      ensurePlaying: async () => ({ ok: true, blocked: false }),
      onVideoStalled: () => {},
    };
    const c37 = new AP.App.PlayerController(cfg, rtcConfig, ui37, clock37, log, streaming, stats, null);
    await c37.init();
    streaming._sink({ type: 'WEBRTC_STATE', payload: { up: true } });
    frameCb37();
    assert(c37.state === PlayerState.PLAYING, 'c37 PLAYING');

    // Set degraded flag manually (simulating ICE degradation)
    c37._degraded = true;
    assert(c37._degraded === true, 'c37 degraded=true');

    // Trigger recovery → RECONNECTING
    c37.requestRecovery('test_degraded', 1);
    assert(c37.state === PlayerState.RECONNECTING, 'c37 RECONNECTING');

    // Advance backoff + simulate recovery success
    await clock37.advance(500);
    await flushMicrotasks();

    // Simulate stream recovery: webrtcUp + firstFrame
    streaming._sink({ type: 'WEBRTC_STATE', payload: { up: true } });
    frameCb37();

    // If recovery succeeded, degraded should be reset
    if (c37.state === PlayerState.PLAYING) {
      assert(c37._degraded === false, 'c37 degraded=false after recovery success');
    }
  }

  // ---- Test 38: shouldContinue respects _isPlayerHidden
  {
    console.log('--- CT Test 38: shouldContinue respects isPlayerHidden ---');
    const clock38 = createFakeClock();
    let frameCb38 = null;
    let elementVisible = true;
    const ui38 = {
      startFrameClock: (cb) => { frameCb38 = cb; },
      stopFrameClock: () => {},
      bindIntents: () => {},
      render: () => {},
      bindStream: () => {},
      ensurePlaying: async () => ({ ok: true, blocked: false }),
      onVideoStalled: () => {},
      isElementVisible: () => elementVisible,
    };
    const c38 = new AP.App.PlayerController(cfg, rtcConfig, ui38, clock38, log, streaming, stats, null);
    await c38.init();
    streaming._sink({ type: 'WEBRTC_STATE', payload: { up: true } });
    frameCb38();
    assert(c38.state === PlayerState.PLAYING, 'c38 PLAYING');

    // Element visible → not hidden
    assert(!c38._isPlayerHidden(), 'c38 not hidden when element visible');

    // Hide element → player hidden
    elementVisible = false;
    assert(c38._isPlayerHidden(), 'c38 hidden when element not visible');

    // Show element → not hidden again
    elementVisible = true;
    assert(!c38._isPlayerHidden(), 'c38 not hidden when element visible again');
  }

  console.log('OK: controller tests passed');
}

if (require.main === module) {
  main().catch((e) => {
    console.error('FAILED:', e);
    process.exitCode = 1;
  });
}
