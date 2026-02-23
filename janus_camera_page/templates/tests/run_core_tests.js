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
  loadScript(sandbox, path.join(root, 'player', 'core', 'state_machine_legacy.js'));
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

  console.log('OK: core tests passed');
}

if (require.main === module) main();
