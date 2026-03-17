/* Minimal unit tests for pure core/ logic (Node, no browser/Janus). */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

function assert(cond, msg){
  if (!cond) {
    throw new Error('Assertion failed: ' + (msg || ''));
  }
}

function loadScript(sandbox, filePath){
  const code = fs.readFileSync(filePath, 'utf8');
  vm.runInContext(code, sandbox, { filename: filePath });
}

function main(){
  const root = path.resolve(__dirname, '..');

  const sandbox = vm.createContext({
    window: {},
    console,
    Math: Math,
  });
  // deterministic jitter
  sandbox.Math.random = () => 0.5;

  // Provide Janus placeholder (not used by core).
  sandbox.Janus = { randomString: () => 'deadbeef' };

  // Load in dependency order.
  loadScript(sandbox, path.join(root, 'player', 'ns.js'));
  loadScript(sandbox, path.join(root, 'player', 'core', 'player_state.js'));
  loadScript(sandbox, path.join(root, 'player', 'core', 'codes.js'));
  loadScript(sandbox, path.join(root, 'player', 'core', 'domain_events.js'));
  loadScript(sandbox, path.join(root, 'player', 'core', 'connection_policy.js'));
  loadScript(sandbox, path.join(__dirname, 'state_machine_legacy.js'));
  loadScript(sandbox, path.join(root, 'player', 'core', 'state_machine_canonical.js'));
  loadScript(sandbox, path.join(root, 'player', 'core', 'invariants.js'));
  loadScript(sandbox, path.join(root, 'player', 'core', 'fail_closed.js'));
  loadScript(sandbox, path.join(root, 'player', 'core', 'backoff.js'));
  loadScript(sandbox, path.join(root, 'player', 'core', 'recovery_policy.js'));

  const AP = sandbox.window.AutonomousPlayer;
  assert(AP && AP.Core, 'AutonomousPlayer.Core exists');

  const S = AP.Core.PlayerState;
  const E = AP.Core.DomainEventType;
  const PA = AP.Core.PolicyAction;
  const RA = AP.Core.RecoveryAction;
  const RR = AP.Core.RecoveryReason;
  const RS = AP.Core.RecoverySeverity;

  // StateMachine.transition
  assert(AP.Core.StateMachine.transition(S.IDLE, E.USER_PLAY) === S.CONNECTING, 'IDLE + USER_PLAY -> CONNECTING');
  assert(AP.Core.StateMachine.transition(S.CONNECTING, E.STREAM_RECOVERED) === S.PLAYING, 'CONNECTING + STREAM_RECOVERED -> PLAYING');
  assert(AP.Core.StateMachine.transition(S.CONNECTING, E.USER_STOP) === S.IDLE, 'CONNECTING + USER_STOP -> IDLE');
  assert(AP.Core.StateMachine.transition(S.PLAYING, E.STREAM_LOST) === S.RECONNECTING, 'PLAYING + STREAM_LOST -> RECONNECTING');
  assert(AP.Core.StateMachine.transition(S.RECONNECTING, E.RECONNECT_EXHAUSTED) === S.ERROR, 'RECONNECTING + RECONNECT_EXHAUSTED -> ERROR');
  assert(AP.Core.StateMachine.transition(S.RECONNECTING, E.STREAM_RECOVERED) === S.PLAYING, 'RECONNECTING + STREAM_RECOVERED -> PLAYING');
  assert(AP.Core.StateMachine.transition(S.ERROR, E.USER_PLAY) === S.CONNECTING, 'ERROR + USER_PLAY -> CONNECTING');
  assert(AP.Core.StateMachine.transition(S.IDLE, E.STREAM_RECOVERED) === null, 'invalid: IDLE + STREAM_RECOVERED -> null');
  assert(AP.Core.StateMachine.transition(S.PLAYING, E.USER_STOP) === S.IDLE, 'PLAYING + USER_STOP -> IDLE');
  assert(AP.Core.StateMachine.transition(S.CONNECTING, E.RECONNECT_SCHEDULED) === S.RECONNECTING, 'CONNECTING + RECONNECT_SCHEDULED -> RECONNECTING');

  // ConnectionPolicy.isConnected
  assert(AP.Core.ConnectionPolicy.isConnected({ webrtcUp: true, firstFrameReceived: true }) === true, 'isConnected both true');
  assert(AP.Core.ConnectionPolicy.isConnected({ webrtcUp: true, firstFrameReceived: false }) === false, 'isConnected firstFrame false');
  assert(AP.Core.ConnectionPolicy.isConnected({ webrtcUp: false, firstFrameReceived: true }) === false, 'isConnected webrtcUp false');
  assert(AP.Core.ConnectionPolicy.isConnected({ webrtcUp: false, firstFrameReceived: false }) === false, 'isConnected both false');
  assert(AP.Core.ConnectionPolicy.isConnected(null) === false, 'isConnected null');
  assert(AP.Core.ConnectionPolicy.isConnected({}) === false, 'isConnected empty');

  // ConnectionPolicy.decide
  let dec = AP.Core.ConnectionPolicy.decide(E.ICE_FAILED, { state: S.CONNECTING, webrtcUp: false, firstFrameReceived: false, desiredPlaying: true });
  assert(dec.action === PA.REQUEST_RECOVERY && dec.reason === RR.ICE_FAILED && dec.severity === RS.HARD, 'ICE_FAILED no frame -> REQUEST_RECOVERY');
  dec = AP.Core.ConnectionPolicy.decide(E.ICE_FAILED, { state: S.PLAYING, webrtcUp: false, firstFrameReceived: true, desiredPlaying: true });
  assert(dec.action === PA.REQUEST_RECOVERY && dec.reason === RR.ICE_FAILED && dec.severity === RS.HARD, 'ICE_FAILED media flowing -> REQUEST_RECOVERY HARD (connection is dead)');
  dec = AP.Core.ConnectionPolicy.decide(E.MEDIA_SILENCE_TIMEOUT, { state: S.PLAYING, firstFrameReceived: true, desiredPlaying: true });
  assert(dec.action === PA.REQUEST_RECOVERY && dec.reason === RR.NO_FRAMES, 'MEDIA_SILENCE_TIMEOUT -> REQUEST_RECOVERY NO_FRAMES');
  dec = AP.Core.ConnectionPolicy.decide(E.MEDIA_SILENCE_TIMEOUT, { state: S.PLAYING, firstFrameReceived: true, desiredPlaying: false });
  assert(dec.action === PA.NO_OP, 'desiredPlaying false -> NO_OP');
  dec = AP.Core.ConnectionPolicy.decide(E.WEBRTC_DOWN, { state: S.PLAYING, webrtcUp: false, firstFrameReceived: true, desiredPlaying: true, webrtcDownReason: 'ice failed' });
  assert(dec.action === PA.MARK_DEGRADED, 'WEBRTC_DOWN ice+media -> MARK_DEGRADED');
  dec = AP.Core.ConnectionPolicy.decide(E.HANGUP, { state: S.PLAYING, firstFrameReceived: true, desiredPlaying: true, hangupReason: 'ice connection' });
  assert(dec.action === PA.MARK_DEGRADED, 'HANGUP ice+media -> MARK_DEGRADED');

  // Backoff tests: jitterRatio 0 => no Math.random(), deterministic (P0_03)
  const cfg = { backoffBaseMs: 500, backoffFactor: 1.8, backoffMinMs: 250, backoffMaxMs: 15000, backoffJitterRatio: 0 };
  const b1 = AP.Core.computeBackoffMs(1, cfg);
  const b2 = AP.Core.computeBackoffMs(2, cfg);
  const b3 = AP.Core.computeBackoffMs(3, cfg);

  assert(b1 === 500, 'attempt1 backoff');
  assert(b2 === 900, 'attempt2 backoff');
  assert(b3 === 1620, 'attempt3 backoff');
  assert(AP.Core.computeBackoffMs(100, cfg) <= 15000, 'max clamp');
  assert(AP.Core.computeBackoffMs(2, cfg) === AP.Core.computeBackoffMs(2, cfg), 'same (attempt, cfg) -> same backoff (deterministic)');

  const cfgJitter = { backoffBaseMs: 500, backoffFactor: 1.8, backoffMinMs: 250, backoffMaxMs: 15000, backoffJitterRatio: 0.25 };
  const seed = 42;
  const withSeed1 = AP.Core.computeBackoffMs(2, cfgJitter, seed);
  const withSeed2 = AP.Core.computeBackoffMs(2, cfgJitter, seed);
  assert(withSeed1 === withSeed2, 'same (attempt, cfg, jitterSeed) -> same backoff (P0_01)');
  assert(withSeed1 >= 250 && withSeed1 <= 15000, 'jitter with seed within bounds');

  // Recovery policy tests
  const policy = { maxWatchRetries: 3, maxReattachRetries: 2 };

  assert(AP.Core.decideRecoveryAction(1, RS.SOFT, policy) === RA.SOFT_RESTART, 'soft attempt1 -> soft restart');
  assert(AP.Core.decideRecoveryAction(3, RS.SOFT, policy) === RA.SOFT_RESTART, 'soft attempt3 -> soft restart');
  assert(AP.Core.decideRecoveryAction(4, RS.SOFT, policy) === RA.REATTACH_PLUGIN, 'soft attempt4 -> reattach');
  assert(AP.Core.decideRecoveryAction(5, RS.SOFT, policy) === RA.REATTACH_PLUGIN, 'soft attempt5 -> reattach');
  assert(AP.Core.decideRecoveryAction(6, RS.SOFT, policy) === RA.RECREATE_SESSION, 'soft attempt6 -> recreate');

  assert(AP.Core.decideRecoveryAction(1, RS.HARD, policy) === RA.RECREATE_SESSION, 'hard severity forces recreate');

  // ---------- StateMachineCanonical: table-driven tests ----------
  const CE = AP.Core.EventType;
  const CA = AP.Core.ActionType;
  const Canonical = AP.Core.StateMachineCanonical;

  function snap(state, overrides) {
    const base = { state, generation: 0, reconnectAttempts: 0, webrtcUp: false, firstFrameReceived: false };
    if (state === S.PLAYING) {
      base.webrtcUp = true;
      base.firstFrameReceived = true;
    }
    return Object.assign({}, base, overrides || {});
  }

  // 2.1 Valid state transitions (table-driven)
  const validTransitions = [
    { from: S.IDLE, event: { type: CE.PLAY_REQUEST }, nextState: S.CONNECTING, actionsContain: CA.START_JANUS },
    { from: S.CONNECTING, event: { type: CE.STOP_REQUEST }, nextState: S.IDLE, actionsContain: CA.CANCEL_ALL_TIMERS },
    { from: S.CONNECTING, event: { type: CE.STREAM_RECOVERED }, nextState: S.PLAYING, actionsContain: CA.RENDER },
    { from: S.CONNECTING, event: { type: CE.RECONNECT_SCHEDULED, reason: 'x', severity: 1 }, nextState: S.RECONNECTING, actionsContain: CA.START_RECONNECT_TIMER },
    { from: S.CONNECTING, event: { type: CE.CONNECT_FAILED, reason: 'err' }, nextState: S.ERROR, actionsContain: CA.LOG },
    { from: S.PLAYING, event: { type: CE.STOP_REQUEST }, nextState: S.IDLE, actionsContain: CA.CANCEL_ALL_TIMERS },
    { from: S.PLAYING, event: { type: CE.WEBRTC_DOWN, reason: 'r' }, nextState: S.RECONNECTING, actionsContain: CA.START_RECONNECT_TIMER },
    { from: S.PLAYING, event: { type: CE.RECONNECT_SCHEDULED }, nextState: S.RECONNECTING, actionsContain: CA.START_RECONNECT_TIMER },
    { from: S.RECONNECTING, event: { type: CE.STOP_REQUEST }, nextState: S.IDLE, actionsContain: CA.CANCEL_ALL_TIMERS },
    { from: S.RECONNECTING, event: { type: CE.RECONNECT_SUCCESS }, nextState: S.PLAYING, actionsContain: CA.RENDER },
    { from: S.RECONNECTING, event: { type: CE.STREAM_RECOVERED }, nextState: S.PLAYING, actionsContain: CA.RENDER },
    { from: S.RECONNECTING, event: { type: CE.RECONNECT_EXHAUSTED, reason: 'ex' }, nextState: S.ERROR, actionsContain: CA.LOG },
    { from: S.ERROR, event: { type: CE.RESET }, nextState: S.CONNECTING, actionsContain: CA.START_JANUS },
    { from: S.ERROR, event: { type: CE.PLAY_REQUEST }, nextState: S.CONNECTING, actionsContain: CA.START_JANUS },
  ];
  validTransitions.forEach(({ from, event, nextState, actionsContain }) => {
    const s = snap(from);
    const r = Canonical.transition(event, s);
    assert(r.next.state === nextState, `valid: ${from} + ${event.type} -> ${nextState}`);
    assert(r.actions.some((a) => a.type === actionsContain), `valid: ${from} + ${event.type} actions contain ${actionsContain}`);
  });

  // 2.2 Invalid transitions (fail-closed)
  const invalidTransitions = [
    { from: S.IDLE, event: { type: CE.STOP_REQUEST } },
    { from: S.IDLE, event: { type: CE.STREAM_RECOVERED } },
    { from: S.IDLE, event: { type: CE.FIRST_FRAME_RECEIVED } },
    { from: S.IDLE, event: { type: CE.WEBRTC_UP } },
    { from: S.CONNECTING, event: { type: CE.PLAY_REQUEST } },
    { from: S.PLAYING, event: { type: CE.CONNECT_FAILED } },
    { from: S.RECONNECTING, event: { type: CE.PLAY_REQUEST } },
    { from: S.RECONNECTING, event: { type: CE.CONNECT_FAILED } },
    { from: S.ERROR, event: { type: CE.STOP_REQUEST } },
    { from: S.ERROR, event: { type: CE.FIRST_FRAME_RECEIVED } },
    { from: S.ERROR, event: { type: CE.RECONNECT_SCHEDULED } },
    { from: S.IDLE, event: { type: 'UNKNOWN_EVENT' } },
  ];
  invalidTransitions.forEach(({ from, event }) => {
    const s = snap(from);
    const r = Canonical.transition(event, s);
    assert(r.next.state === S.ERROR, `invalid: ${from} + ${event.type} -> ERROR (fail-closed)`);
    assert(r.actions.some((a) => a.type === CA.LOG), `invalid: ${from} + ${event.type} actions include LOG`);
    assert(r.actions.some((a) => a.type === CA.RENDER), `invalid: ${from} + ${event.type} actions include RENDER (UI must update on ERROR)`);
  });

  // 2.3 FORCE_ERROR from any state -> ERROR and generation bumped by 1
  [S.IDLE, S.CONNECTING, S.PLAYING, S.RECONNECTING, S.ERROR].forEach((from) => {
    const s = snap(from);
    s.generation = 5;
    const r = Canonical.transition({ type: CE.FORCE_ERROR, reason: 'force' }, s);
    assert(r.next.state === S.ERROR, `FORCE_ERROR from ${from} -> ERROR`);
    assert(r.next.generation === 6, `FORCE_ERROR bumps generation: ${s.generation} -> ${r.next.generation}`);
    assert(r.actions.some((a) => a.type === CA.CANCEL_ALL_TIMERS), 'FORCE_ERROR includes CANCEL_ALL_TIMERS');
    assert(r.actions.some((a) => a.type === CA.RENDER), 'FORCE_ERROR includes RENDER (UI must update on ERROR)');
  });

  // 2.4 FIRST_FRAME_RECEIVED: CONNECTING/RECONNECTING -> report firstFrameReceived: true; IDLE/ERROR -> fail-closed
  const snapConn = snap(S.CONNECTING, { webrtcUp: true });
  const rFirstConn = Canonical.transition({ type: CE.FIRST_FRAME_RECEIVED }, snapConn);
  assert(rFirstConn.next.state === S.CONNECTING, 'CONNECTING + FIRST_FRAME_RECEIVED -> state unchanged');
  assert(rFirstConn.next.firstFrameReceived === true, 'CONNECTING + FIRST_FRAME_RECEIVED -> firstFrameReceived true');

  const snapReconn = { state: S.RECONNECTING, generation: 1, reconnectAttempts: 1, webrtcUp: true, firstFrameReceived: false };
  const rFirstReconn = Canonical.transition({ type: CE.FIRST_FRAME_RECEIVED }, snapReconn);
  assert(rFirstReconn.next.state === S.RECONNECTING, 'RECONNECTING + FIRST_FRAME_RECEIVED -> state unchanged');
  assert(rFirstReconn.next.firstFrameReceived === true, 'RECONNECTING + FIRST_FRAME_RECEIVED -> firstFrameReceived true');

  const rFirstIdle = Canonical.transition({ type: CE.FIRST_FRAME_RECEIVED }, snap(S.IDLE));
  assert(rFirstIdle.next.state === S.ERROR, 'IDLE + FIRST_FRAME_RECEIVED -> fail-closed ERROR');
  const rFirstErr = Canonical.transition({ type: CE.FIRST_FRAME_RECEIVED }, snap(S.ERROR));
  assert(rFirstErr.next.state === S.ERROR, 'ERROR + FIRST_FRAME_RECEIVED -> fail-closed ERROR');

  // RECOVERY_ATTEMPT_STARTED: RECONNECTING -> webrtcUp false, firstFrameReceived false
  const snapReconn2 = { state: S.RECONNECTING, generation: 2, reconnectAttempts: 1, webrtcUp: true, firstFrameReceived: true };
  const rRecovery = Canonical.transition({ type: CE.RECOVERY_ATTEMPT_STARTED }, snapReconn2);
  assert(rRecovery.next.state === S.RECONNECTING, 'RECONNECTING + RECOVERY_ATTEMPT_STARTED -> state unchanged');
  assert(rRecovery.next.webrtcUp === false, 'RECOVERY_ATTEMPT_STARTED -> webrtcUp false');
  assert(rRecovery.next.firstFrameReceived === false, 'RECOVERY_ATTEMPT_STARTED -> firstFrameReceived false');

  // 2.5 Idempotency: RECONNECTING + RECONNECT_SCHEDULED -> same state, no extra actions
  const snapReconnecting = { state: S.RECONNECTING, generation: 1, reconnectAttempts: 1, webrtcUp: false, firstFrameReceived: true };
  const rIdem = Canonical.transition({ type: CE.RECONNECT_SCHEDULED }, snapReconnecting);
  assert(rIdem.next.state === S.RECONNECTING, 'idempotent: RECONNECTING + RECONNECT_SCHEDULED -> same state');
  assert(rIdem.actions.length === 0, 'idempotent: no actions');

  // Idempotency: PLAYING + PLAY_REQUEST -> same state (no-op), RENDER
  const rPlayIdem = Canonical.transition({ type: CE.PLAY_REQUEST }, snap(S.PLAYING));
  assert(rPlayIdem.next.state === S.PLAYING, 'idempotent: PLAYING + PLAY_REQUEST -> same state');
  assert(rPlayIdem.actions.some((a) => a.type === CA.RENDER), 'idempotent: PLAYING + PLAY_REQUEST includes RENDER');

  // Invalid snapshot -> fail-closed
  const rBadSnap = Canonical.transition({ type: CE.PLAY_REQUEST }, null);
  assert(rBadSnap.next.state === S.ERROR, 'null snapshot -> ERROR');
  const rBadSnap2 = Canonical.transition({ type: CE.PLAY_REQUEST }, {});
  assert(rBadSnap2.next.state === S.ERROR, 'empty snapshot -> ERROR');

  // 2.6 Duality guard: for overlapping (state, event), StateMachineCanonical.next.state matches StateMachine (P1_03)
  const Simple = AP.Core.StateMachine;
  const DE = AP.Core.DomainEventType;
  const overlapPairs = [
    { from: S.IDLE, canonicalEvent: CE.PLAY_REQUEST, domainEvent: DE.USER_PLAY, nextState: S.CONNECTING },
    { from: S.CONNECTING, canonicalEvent: CE.STOP_REQUEST, domainEvent: DE.USER_STOP, nextState: S.IDLE },
    { from: S.CONNECTING, canonicalEvent: CE.STREAM_RECOVERED, domainEvent: DE.STREAM_RECOVERED, nextState: S.PLAYING },
    { from: S.CONNECTING, canonicalEvent: CE.RECONNECT_SCHEDULED, domainEvent: DE.RECONNECT_SCHEDULED, nextState: S.RECONNECTING },
    { from: S.PLAYING, canonicalEvent: CE.STOP_REQUEST, domainEvent: DE.USER_STOP, nextState: S.IDLE },
    { from: S.PLAYING, canonicalEvent: CE.RECONNECT_SCHEDULED, domainEvent: DE.RECONNECT_SCHEDULED, nextState: S.RECONNECTING },
    { from: S.RECONNECTING, canonicalEvent: CE.STOP_REQUEST, domainEvent: DE.USER_STOP, nextState: S.IDLE },
    { from: S.RECONNECTING, canonicalEvent: CE.STREAM_RECOVERED, domainEvent: DE.STREAM_RECOVERED, nextState: S.PLAYING },
    { from: S.RECONNECTING, canonicalEvent: CE.RECONNECT_SUCCESS, domainEvent: DE.RECONNECT_SUCCESS, nextState: S.PLAYING },
    { from: S.RECONNECTING, canonicalEvent: CE.RECONNECT_EXHAUSTED, domainEvent: DE.RECONNECT_EXHAUSTED, nextState: S.ERROR },
    { from: S.ERROR, canonicalEvent: CE.PLAY_REQUEST, domainEvent: DE.USER_PLAY, nextState: S.CONNECTING },
  ];
  overlapPairs.forEach(({ from, canonicalEvent, domainEvent, nextState }) => {
    const r = Canonical.transition({ type: canonicalEvent }, snap(from));
    const simpleNext = Simple.transition(from, domainEvent);
    assert(r.next.state === nextState, `canonical: ${from} + ${canonicalEvent} -> ${nextState}`);
    assert(simpleNext === nextState, `simple: ${from} + ${domainEvent} -> ${nextState}`);
    assert(r.next.state === simpleNext, `duality: canonical and simple agree for ${from} + ${canonicalEvent}`);
  });

  // ---------- InvariantGate (P0_02: L4/L5/L6 full coverage) ----------
  const Gate = AP.Core.InvariantGate;
  const Violation = AP.Core.InvariantViolation;

  const validSnapshots = [
    { state: S.IDLE },
    { state: S.IDLE, webrtcUp: false, firstFrameReceived: false },
    { state: S.CONNECTING },
    { state: S.PLAYING, webrtcUp: true, firstFrameReceived: true },
    { state: S.RECONNECTING, reconnectAttempts: 1 },
    { state: S.RECONNECTING, reconnectAttempts: 2, webrtcUp: false, firstFrameReceived: false },
    { state: S.ERROR, webrtcUp: false, firstFrameReceived: false },
  ];
  validSnapshots.forEach((s) => {
    try {
      Gate.check(s);
    } catch (e) {
      throw new Error(`InvariantGate should accept valid snapshot: ${JSON.stringify(s)} - ${e.message}`);
    }
  });

  const invalidSnapshots = [
    { snap: { state: S.PLAYING, webrtcUp: false }, expectedId: 'L4' },
    { snap: { state: S.PLAYING, webrtcUp: true, firstFrameReceived: false }, expectedId: 'L4' },
    { snap: { state: S.RECONNECTING, reconnectAttempts: 0 }, expectedId: 'L5' },
    { snap: { state: S.RECONNECTING }, expectedId: 'L5' },
    { snap: { state: S.IDLE, webrtcUp: true }, expectedId: 'L6' },
    { snap: { state: S.ERROR, webrtcUp: true }, expectedId: 'L6' },
    { snap: { state: S.ERROR, firstFrameReceived: true }, expectedId: 'L6' },
  ];
  invalidSnapshots.forEach(({ snap, expectedId }) => {
    let threw = false;
    try {
      Gate.check(snap);
    } catch (e) {
      threw = e instanceof Violation && e.id === expectedId;
    }
    assert(threw, `InvariantGate should throw ${expectedId} for ${JSON.stringify(snap)}`);
  });

  // L7: from ERROR only RESET and PLAY_REQUEST lead out; others fail-closed
  const errorSnap = snap(S.ERROR);
  const rReset = Canonical.transition({ type: CE.RESET }, errorSnap);
  const rPlay = Canonical.transition({ type: CE.PLAY_REQUEST }, errorSnap);
  assert(rReset.next.state === S.CONNECTING, 'ERROR + RESET -> CONNECTING (L7)');
  assert(rPlay.next.state === S.CONNECTING, 'ERROR + PLAY_REQUEST -> CONNECTING (L7)');
  const rStop = Canonical.transition({ type: CE.STOP_REQUEST }, errorSnap);
  assert(rStop.next.state === S.ERROR, 'ERROR + STOP_REQUEST -> fail-closed (L7)');

  // STREAMING_OFFER_RECEIVED in CONNECTING and RECONNECTING (state unchanged)
  const rOfferConn = Canonical.transition({ type: CE.STREAMING_OFFER_RECEIVED }, snap(S.CONNECTING));
  assert(rOfferConn.next.state === S.CONNECTING, 'CONNECTING + STREAMING_OFFER_RECEIVED -> state unchanged');
  const rOfferReconn = Canonical.transition({ type: CE.STREAMING_OFFER_RECEIVED }, snap(S.RECONNECTING, { reconnectAttempts: 1 }));
  assert(rOfferReconn.next.state === S.RECONNECTING, 'RECONNECTING + STREAMING_OFFER_RECEIVED -> state unchanged');

  // ═══════════════════════════════════════════════════════════════════
  // Extended coverage: connection_policy.decide() — all 13 event types
  // ═══════════════════════════════════════════════════════════════════

  // ICE_DISCONNECTED_GRACE_TIMEOUT — fresh frames → MARK_DEGRADED
  dec = AP.Core.ConnectionPolicy.decide(E.ICE_DISCONNECTED_GRACE_TIMEOUT, {
    state: S.PLAYING, webrtcUp: false, firstFrameReceived: true, desiredPlaying: true, lastFrameAgeMs: 500,
  });
  assert(dec.action === PA.MARK_DEGRADED, 'ICE_DISCONNECTED_GRACE_TIMEOUT fresh frame → MARK_DEGRADED');

  // ICE_DISCONNECTED_GRACE_TIMEOUT — stale frames (>2000ms) → REQUEST_RECOVERY
  dec = AP.Core.ConnectionPolicy.decide(E.ICE_DISCONNECTED_GRACE_TIMEOUT, {
    state: S.PLAYING, webrtcUp: false, firstFrameReceived: true, desiredPlaying: true, lastFrameAgeMs: 3000,
  });
  assert(dec.action === PA.REQUEST_RECOVERY, 'ICE_DISCONNECTED_GRACE_TIMEOUT stale → REQUEST_RECOVERY');
  assert(dec.reason === RR.ICE_DISCONNECTED_GRACE, 'ICE_DISCONNECTED_GRACE_TIMEOUT stale → reason ICE_DISCONNECTED_GRACE');
  assert(dec.severity === RS.MEDIUM, 'ICE_DISCONNECTED_GRACE_TIMEOUT stale → severity MEDIUM');

  // ICE_DISCONNECTED_GRACE_TIMEOUT — no firstFrame → REQUEST_RECOVERY
  dec = AP.Core.ConnectionPolicy.decide(E.ICE_DISCONNECTED_GRACE_TIMEOUT, {
    state: S.CONNECTING, firstFrameReceived: false, desiredPlaying: true, lastFrameAgeMs: 0,
  });
  assert(dec.action === PA.REQUEST_RECOVERY, 'ICE_DISCONNECTED_GRACE_TIMEOUT no firstFrame → REQUEST_RECOVERY');

  // TRACK_MUTE_TIMEOUT → REQUEST_RECOVERY(TRACK_MUTED, MEDIUM)
  dec = AP.Core.ConnectionPolicy.decide(E.TRACK_MUTE_TIMEOUT, {
    state: S.PLAYING, firstFrameReceived: true, desiredPlaying: true,
  });
  assert(dec.action === PA.REQUEST_RECOVERY, 'TRACK_MUTE_TIMEOUT → REQUEST_RECOVERY');
  assert(dec.reason === RR.TRACK_MUTED, 'TRACK_MUTE_TIMEOUT → reason TRACK_MUTED');
  assert(dec.severity === RS.MEDIUM, 'TRACK_MUTE_TIMEOUT → severity MEDIUM');

  // TRACK_ENDED → REQUEST_RECOVERY(NO_FRAMES, MEDIUM)
  dec = AP.Core.ConnectionPolicy.decide(E.TRACK_ENDED, {
    state: S.PLAYING, firstFrameReceived: true, desiredPlaying: true,
  });
  assert(dec.action === PA.REQUEST_RECOVERY, 'TRACK_ENDED → REQUEST_RECOVERY');
  assert(dec.reason === RR.NO_FRAMES, 'TRACK_ENDED → reason NO_FRAMES');

  // SESSION_RESET → REQUEST_RECOVERY(SESSION_RESET, HARD)
  dec = AP.Core.ConnectionPolicy.decide(E.SESSION_RESET, {
    state: S.PLAYING, firstFrameReceived: true, desiredPlaying: true,
  });
  assert(dec.action === PA.REQUEST_RECOVERY, 'SESSION_RESET → REQUEST_RECOVERY');
  assert(dec.reason === RR.SESSION_RESET, 'SESSION_RESET → reason SESSION_RESET');
  assert(dec.severity === RS.HARD, 'SESSION_RESET → severity HARD');

  // JANUS_ERROR → REQUEST_RECOVERY(JANUS_ERROR, MEDIUM)
  dec = AP.Core.ConnectionPolicy.decide(E.JANUS_ERROR, {
    state: S.PLAYING, firstFrameReceived: true, desiredPlaying: true,
  });
  assert(dec.action === PA.REQUEST_RECOVERY, 'JANUS_ERROR → REQUEST_RECOVERY');
  assert(dec.reason === RR.JANUS_ERROR, 'JANUS_ERROR → reason JANUS_ERROR');
  assert(dec.severity === RS.MEDIUM, 'JANUS_ERROR → severity MEDIUM');

  // FPS_DROP → REQUEST_RECOVERY(FPS_DROP, MEDIUM)
  dec = AP.Core.ConnectionPolicy.decide(E.FPS_DROP, {
    state: S.PLAYING, firstFrameReceived: true, desiredPlaying: true,
  });
  assert(dec.action === PA.REQUEST_RECOVERY, 'FPS_DROP → REQUEST_RECOVERY');
  assert(dec.reason === RR.FPS_DROP, 'FPS_DROP → reason FPS_DROP');
  assert(dec.severity === RS.MEDIUM, 'FPS_DROP → severity MEDIUM');

  // VIDEO_STALLED → REQUEST_RECOVERY(VIDEO_STALLED, MEDIUM)
  dec = AP.Core.ConnectionPolicy.decide(E.VIDEO_STALLED, {
    state: S.PLAYING, firstFrameReceived: true, desiredPlaying: true,
  });
  assert(dec.action === PA.REQUEST_RECOVERY, 'VIDEO_STALLED → REQUEST_RECOVERY');
  assert(dec.reason === RR.VIDEO_STALLED, 'VIDEO_STALLED → reason VIDEO_STALLED');
  assert(dec.severity === RS.MEDIUM, 'VIDEO_STALLED → severity MEDIUM');

  // WEBRTC_DOWN — no "ice" in reason → MEDIUM (not HARD)
  dec = AP.Core.ConnectionPolicy.decide(E.WEBRTC_DOWN, {
    state: S.PLAYING, webrtcUp: false, firstFrameReceived: true, desiredPlaying: true, webrtcDownReason: 'server closed',
  });
  assert(dec.action === PA.REQUEST_RECOVERY, 'WEBRTC_DOWN no-ice → REQUEST_RECOVERY');
  assert(dec.reason === RR.WEBRTC_DOWN, 'WEBRTC_DOWN no-ice → reason WEBRTC_DOWN');
  assert(dec.severity === RS.MEDIUM, 'WEBRTC_DOWN no-ice → severity MEDIUM');

  // WEBRTC_DOWN — "ice" in reason + no media → HARD
  dec = AP.Core.ConnectionPolicy.decide(E.WEBRTC_DOWN, {
    state: S.CONNECTING, webrtcUp: false, firstFrameReceived: false, desiredPlaying: true, webrtcDownReason: 'ice failed',
  });
  assert(dec.severity === RS.HARD, 'WEBRTC_DOWN ice+no-media → severity HARD');

  // HANGUP — no "ice" in reason → MEDIUM
  dec = AP.Core.ConnectionPolicy.decide(E.HANGUP, {
    state: S.PLAYING, firstFrameReceived: true, desiredPlaying: true, hangupReason: 'server hangup',
  });
  assert(dec.action === PA.REQUEST_RECOVERY, 'HANGUP no-ice → REQUEST_RECOVERY');
  assert(dec.reason === RR.HANGUP, 'HANGUP no-ice → reason HANGUP');
  assert(dec.severity === RS.MEDIUM, 'HANGUP no-ice → severity MEDIUM');

  // HANGUP — "ice" in reason + no media → HARD
  dec = AP.Core.ConnectionPolicy.decide(E.HANGUP, {
    state: S.CONNECTING, firstFrameReceived: false, desiredPlaying: true, hangupReason: 'ice connection',
  });
  assert(dec.severity === RS.HARD, 'HANGUP ice+no-media → severity HARD');

  // Unknown event → NO_OP
  dec = AP.Core.ConnectionPolicy.decide('TOTALLY_UNKNOWN', {
    state: S.PLAYING, firstFrameReceived: true, desiredPlaying: true,
  });
  assert(dec.action === PA.NO_OP, 'unknown event → NO_OP');

  // desiredPlaying false in non-active state → NO_OP (already tested once, adding for coverage completeness)
  dec = AP.Core.ConnectionPolicy.decide(E.VIDEO_STALLED, {
    state: S.PLAYING, firstFrameReceived: true, desiredPlaying: false,
  });
  assert(dec.action === PA.NO_OP, 'VIDEO_STALLED + desiredPlaying false → NO_OP');

  // ═══════════════════════════════════════════════════════════════════
  // Extended coverage: StateMachineCanonical — handleActiveCommon events
  // ═══════════════════════════════════════════════════════════════════

  // WEBRTC_REPORT from CONNECTING → updates webrtcUp, emits RENDER
  let rReport = Canonical.transition({ type: CE.WEBRTC_REPORT, webrtcUp: true }, snap(S.CONNECTING));
  assert(rReport.next.state === S.CONNECTING, 'CONNECTING + WEBRTC_REPORT → state unchanged');
  assert(rReport.next.webrtcUp === true, 'CONNECTING + WEBRTC_REPORT → webrtcUp updated');
  assert(rReport.actions.some((a) => a.type === CA.RENDER), 'CONNECTING + WEBRTC_REPORT → RENDER');

  // WEBRTC_REPORT from PLAYING
  rReport = Canonical.transition({ type: CE.WEBRTC_REPORT, webrtcUp: false }, snap(S.PLAYING));
  assert(rReport.next.state === S.PLAYING, 'PLAYING + WEBRTC_REPORT → state unchanged');
  assert(rReport.next.webrtcUp === false, 'PLAYING + WEBRTC_REPORT → webrtcUp updated to false');

  // WEBRTC_REPORT from RECONNECTING
  rReport = Canonical.transition({ type: CE.WEBRTC_REPORT, webrtcUp: true }, snap(S.RECONNECTING, { reconnectAttempts: 1 }));
  assert(rReport.next.state === S.RECONNECTING, 'RECONNECTING + WEBRTC_REPORT → state unchanged');
  assert(rReport.next.webrtcUp === true, 'RECONNECTING + WEBRTC_REPORT → webrtcUp true');

  // ICE_REPORT disconnected → ARM_ICE_GRACE
  let rIce = Canonical.transition({ type: CE.ICE_REPORT, iceState: 'disconnected' }, snap(S.PLAYING));
  assert(rIce.next.state === S.PLAYING, 'PLAYING + ICE_REPORT disconnected → state unchanged');
  assert(rIce.next.iceState === 'disconnected', 'PLAYING + ICE_REPORT → iceState updated');
  assert(rIce.actions.some((a) => a.type === CA.ARM_ICE_GRACE), 'ICE_REPORT disconnected → ARM_ICE_GRACE');

  // ICE_REPORT connected → CANCEL_ICE_GRACE
  rIce = Canonical.transition({ type: CE.ICE_REPORT, iceState: 'connected' }, snap(S.PLAYING));
  assert(rIce.actions.some((a) => a.type === CA.CANCEL_ICE_GRACE), 'ICE_REPORT connected → CANCEL_ICE_GRACE');

  // ICE_REPORT completed → CANCEL_ICE_GRACE
  rIce = Canonical.transition({ type: CE.ICE_REPORT, iceState: 'completed' }, snap(S.PLAYING));
  assert(rIce.actions.some((a) => a.type === CA.CANCEL_ICE_GRACE), 'ICE_REPORT completed → CANCEL_ICE_GRACE');

  // ICE_REPORT other (e.g. 'checking') → only RENDER
  rIce = Canonical.transition({ type: CE.ICE_REPORT, iceState: 'checking' }, snap(S.PLAYING));
  assert(!rIce.actions.some((a) => a.type === CA.ARM_ICE_GRACE), 'ICE_REPORT checking → no ARM_ICE_GRACE');
  assert(!rIce.actions.some((a) => a.type === CA.CANCEL_ICE_GRACE), 'ICE_REPORT checking → no CANCEL_ICE_GRACE');
  assert(rIce.actions.some((a) => a.type === CA.RENDER), 'ICE_REPORT checking → RENDER');

  // TRACK_MUTED → ARM_TRACK_MUTE_TIMER with trackId
  let rTrack = Canonical.transition({ type: CE.TRACK_MUTED, trackId: 'v0' }, snap(S.PLAYING));
  assert(rTrack.next.state === S.PLAYING, 'PLAYING + TRACK_MUTED → state unchanged');
  assert(rTrack.actions.some((a) => a.type === CA.ARM_TRACK_MUTE_TIMER && a.trackId === 'v0'), 'TRACK_MUTED → ARM_TRACK_MUTE_TIMER with trackId');

  // TRACK_UNMUTED → DISARM_TRACK_MUTE_TIMER
  rTrack = Canonical.transition({ type: CE.TRACK_UNMUTED, trackId: 'v0' }, snap(S.PLAYING));
  assert(rTrack.actions.some((a) => a.type === CA.DISARM_TRACK_MUTE_TIMER && a.trackId === 'v0'), 'TRACK_UNMUTED → DISARM_TRACK_MUTE_TIMER');

  // TRACK_READY → BIND_STREAM + RENDER
  rTrack = Canonical.transition({ type: CE.TRACK_READY }, snap(S.CONNECTING));
  assert(rTrack.actions.some((a) => a.type === CA.BIND_STREAM), 'TRACK_READY → BIND_STREAM');
  assert(rTrack.actions.some((a) => a.type === CA.RENDER), 'TRACK_READY → RENDER');

  // POLICY_MARK_DEGRADED → MARK_DEGRADED + RENDER
  let rDeg = Canonical.transition({ type: CE.POLICY_MARK_DEGRADED }, snap(S.PLAYING));
  assert(rDeg.actions.some((a) => a.type === CA.MARK_DEGRADED), 'POLICY_MARK_DEGRADED → MARK_DEGRADED');
  assert(rDeg.actions.some((a) => a.type === CA.RENDER), 'POLICY_MARK_DEGRADED → RENDER');

  // PLAYING + ICE_FAILED → RECONNECTING (was only tested from CONNECTING)
  let rIceF = Canonical.transition({ type: CE.ICE_FAILED }, snap(S.PLAYING));
  assert(rIceF.next.state === S.RECONNECTING, 'PLAYING + ICE_FAILED → RECONNECTING');
  assert(rIceF.actions.some((a) => a.type === CA.START_RECONNECT_TIMER), 'PLAYING + ICE_FAILED → START_RECONNECT_TIMER');

  // RECONNECTING + STREAMING_OFFER_RECEIVED → START_RECONNECT_SETTLE
  let rOfferReconnSO = Canonical.transition({ type: CE.STREAMING_OFFER_RECEIVED }, snap(S.RECONNECTING, { reconnectAttempts: 1 }));
  assert(rOfferReconnSO.actions.some((a) => a.type === CA.START_RECONNECT_SETTLE), 'RECONNECTING + STREAMING_OFFER_RECEIVED → START_RECONNECT_SETTLE');

  // PLAYING + STREAMING_OFFER_RECEIVED → RENDER (no START_RECONNECT_SETTLE)
  let rOfferPlay = Canonical.transition({ type: CE.STREAMING_OFFER_RECEIVED }, snap(S.PLAYING));
  assert(rOfferPlay.next.state === S.PLAYING, 'PLAYING + STREAMING_OFFER_RECEIVED → state unchanged');
  assert(rOfferPlay.actions.some((a) => a.type === CA.RENDER), 'PLAYING + STREAMING_OFFER_RECEIVED → RENDER');
  assert(!rOfferPlay.actions.some((a) => a.type === CA.START_RECONNECT_SETTLE), 'PLAYING + STREAMING_OFFER_RECEIVED → no START_RECONNECT_SETTLE');

  // ═══════════════════════════════════════════════════════════════════
  // Extended coverage: backoff edge cases
  // ═══════════════════════════════════════════════════════════════════

  // Attempt 0 → treated as attempt 1
  const b0 = AP.Core.computeBackoffMs(0, cfg);
  assert(b0 === b1, 'attempt 0 → same as attempt 1 (floor)');

  // Negative attempt → treated as attempt 1
  const bNeg = AP.Core.computeBackoffMs(-5, cfg);
  assert(bNeg === b1, 'negative attempt → same as attempt 1');

  // Jitter with ratio > 0 → result ≥ backoffMinMs
  const cfgHighJitter = { backoffBaseMs: 300, backoffFactor: 1.8, backoffMinMs: 150, backoffMaxMs: 15000, backoffJitterRatio: 0.5 };
  for (let a = 1; a <= 10; a++) {
    const bjResult = AP.Core.computeBackoffMs(a, cfgHighJitter, 999);
    assert(bjResult >= 150, `jitter attempt ${a}: result ${bjResult} >= backoffMinMs 150`);
  }

  // Null config → uses defaults, no crash
  const bNullCfg = AP.Core.computeBackoffMs(1, null);
  assert(typeof bNullCfg === 'number' && Number.isFinite(bNullCfg), 'null cfg → returns finite number');
  assert(bNullCfg === 500, 'null cfg → uses default backoffBaseMs 500 for attempt 1');

  // Empty config → uses defaults
  const bEmptyCfg = AP.Core.computeBackoffMs(1, {});
  assert(bEmptyCfg === 500, 'empty cfg → uses default backoffBaseMs 500');

  // ═══════════════════════════════════════════════════════════════════
  // Extended coverage: recovery_policy edge cases
  // ═══════════════════════════════════════════════════════════════════

  // MEDIUM severity → same as SOFT (stays within ladder)
  assert(AP.Core.decideRecoveryAction(1, RS.MEDIUM, policy) === RA.SOFT_RESTART, 'medium attempt1 → soft restart');
  assert(AP.Core.decideRecoveryAction(4, RS.MEDIUM, policy) === RA.REATTACH_PLUGIN, 'medium attempt4 → reattach');
  assert(AP.Core.decideRecoveryAction(6, RS.MEDIUM, policy) === RA.RECREATE_SESSION, 'medium attempt6 → recreate');

  // Attempt 0 → clamped to 1 → SOFT_RESTART
  assert(AP.Core.decideRecoveryAction(0, RS.SOFT, policy) === RA.SOFT_RESTART, 'attempt 0 → clamped to 1 → soft restart');

  // Negative attempt → clamped to 1
  assert(AP.Core.decideRecoveryAction(-3, RS.SOFT, policy) === RA.SOFT_RESTART, 'negative attempt → clamped to 1');

  // Null config → uses defaults (maxWatchRetries=3, maxReattachRetries=2)
  assert(AP.Core.decideRecoveryAction(1, RS.SOFT, null) === RA.SOFT_RESTART, 'null cfg → default watch retries 3');
  assert(AP.Core.decideRecoveryAction(4, RS.SOFT, null) === RA.REATTACH_PLUGIN, 'null cfg → default reattach at 4');
  assert(AP.Core.decideRecoveryAction(6, RS.SOFT, null) === RA.RECREATE_SESSION, 'null cfg → default recreate at 6');

  // Boundary: exactly at watchMax and reattachMax edges
  const policyEdge = { maxWatchRetries: 2, maxReattachRetries: 1 };
  assert(AP.Core.decideRecoveryAction(2, RS.SOFT, policyEdge) === RA.SOFT_RESTART, 'edge: attempt=watchMax → SOFT');
  assert(AP.Core.decideRecoveryAction(3, RS.SOFT, policyEdge) === RA.REATTACH_PLUGIN, 'edge: attempt=watchMax+1 → REATTACH');
  assert(AP.Core.decideRecoveryAction(4, RS.SOFT, policyEdge) === RA.RECREATE_SESSION, 'edge: attempt>watchMax+reattachMax → RECREATE');

  // Severity NaN → clamped to SOFT
  assert(AP.Core.decideRecoveryAction(1, NaN, policy) === RA.SOFT_RESTART, 'NaN severity → treated as SOFT');

  // Severity > HARD → clamped to HARD → RECREATE
  assert(AP.Core.decideRecoveryAction(1, 99, policy) === RA.RECREATE_SESSION, 'severity 99 → clamped to HARD → RECREATE');

  // ═══════════════════════════════════════════════════════════════════
  // Extended coverage: statusTextFor()
  // ═══════════════════════════════════════════════════════════════════

  assert(AP.Core.statusTextFor(S.IDLE) === 'IDLE', 'statusTextFor IDLE');
  assert(AP.Core.statusTextFor(S.CONNECTING) === 'CONNECTING…', 'statusTextFor CONNECTING');
  assert(AP.Core.statusTextFor(S.PLAYING) === 'PLAYING', 'statusTextFor PLAYING');
  assert(AP.Core.statusTextFor(S.RECONNECTING, 3) === 'RECONNECTING… (attempt 3)', 'statusTextFor RECONNECTING with attempt');
  assert(AP.Core.statusTextFor(S.RECONNECTING) === 'RECONNECTING… (attempt 1)', 'statusTextFor RECONNECTING default attempt 1');
  assert(AP.Core.statusTextFor(S.ERROR, 0, 'RECONNECT_EXHAUSTED') === 'ERROR: RECONNECT_EXHAUSTED', 'statusTextFor ERROR with code');
  assert(AP.Core.statusTextFor(S.ERROR) === 'ERROR: unknown', 'statusTextFor ERROR default');
  assert(AP.Core.statusTextFor('WEIRD_STATE') === 'WEIRD_STATE', 'statusTextFor unknown state → String(state)');
  assert(AP.Core.statusTextFor(null) === 'UNKNOWN', 'statusTextFor null → UNKNOWN');

  // ═══════════════════════════════════════════════════════════════════
  // Extended coverage: state_machine_legacy gaps
  // ═══════════════════════════════════════════════════════════════════

  // STREAM_LOST from PLAYING → RECONNECTING
  assert(Simple.transition(S.PLAYING, DE.STREAM_LOST) === S.RECONNECTING, 'legacy: PLAYING + STREAM_LOST → RECONNECTING');

  // RECONNECT_SUCCESS from RECONNECTING → PLAYING
  assert(Simple.transition(S.RECONNECTING, DE.RECONNECT_SUCCESS) === S.PLAYING, 'legacy: RECONNECTING + RECONNECT_SUCCESS → PLAYING');

  // Unknown state → null
  assert(Simple.transition('GARBAGE', DE.USER_PLAY) === null, 'legacy: unknown state → null');

  // Unknown event from valid state → null
  assert(Simple.transition(S.IDLE, 'GARBAGE_EVENT') === null, 'legacy: unknown event → null');

  console.log('OK: core tests passed');
}

if (require.main === module) main();
