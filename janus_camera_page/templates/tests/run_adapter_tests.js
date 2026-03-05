/* Unit tests for adapter layer — JanusSessionManager, JanusStreamingAdapter, JanusTextRoomAdapter, DomUIAdapter (Node, no browser). */
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

// ═══════════════════════════════════════════════════════════════
// Fake clock
// ═══════════════════════════════════════════════════════════════
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

  function advance(ms) {
    const target = now + Math.max(0, Math.trunc(ms || 0));
    while (true) {
      let nextAt = Infinity, nextTimeoutId = null, nextIntervalId = null;
      for (const [id, t] of timeouts.entries()) {
        if (t.at <= target && t.at < nextAt) { nextAt = t.at; nextTimeoutId = id; nextIntervalId = null; }
      }
      for (const [id, t] of intervals.entries()) {
        if (t.nextAt <= target && t.nextAt < nextAt) { nextAt = t.nextAt; nextIntervalId = id; nextTimeoutId = null; }
      }
      if (nextTimeoutId == null && nextIntervalId == null) break;
      now = nextAt;
      if (nextTimeoutId != null) {
        const t = timeouts.get(nextTimeoutId); timeouts.delete(nextTimeoutId);
        if (t && typeof t.fn === 'function') t.fn();
      } else {
        const t = intervals.get(nextIntervalId);
        if (t && typeof t.fn === 'function') { t.nextAt += t.every; t.fn(); }
      }
    }
    now = target;
  }

  return { nowMs: () => now, setTimeout: setTimeoutFn, clearTimeout: clearTimeoutFn, setInterval: setIntervalFn, clearInterval: clearIntervalFn, advance, pendingTimers: () => timeouts.size + intervals.size };
}

// ═══════════════════════════════════════════════════════════════
// Fake Janus constructor & helpers
// ═══════════════════════════════════════════════════════════════
function createJanusStub() {
  const instances = [];

  function JanusConstructor(opts) {
    const inst = {
      opts,
      attachedPlugins: [],
      _connected: true,
      isConnected: () => inst._connected,
      attach: (pluginOpts) => {
        const handle = {
          plugin: pluginOpts.plugin,
          callbacks: pluginOpts,
          webrtcStuff: { pc: { mock: 'peerConnection' } },
          send: (args) => { handle._lastSend = args; if (args.success) args.success(args.message); },
          createAnswer: (args) => { if (args.success) args.success({ type: 'answer', sdp: 'mock-sdp' }); },
          detach: (args) => { if (args && args.success) args.success(); },
          data: (args) => { handle._lastData = args; if (args.error) { /* no error by default */ } },
          _lastSend: null,
          _lastData: null,
        };
        inst.attachedPlugins.push(handle);
        if (pluginOpts.success) pluginOpts.success(handle);
      },
      destroy: (args) => {
        inst._connected = false;
        if (args && args.success) args.success();
      },
    };
    instances.push(inst);
    // Call success async-like (but synchronous here)
    if (opts.success) opts.success();
    return inst; // `new Janus(...)` must return the instance object
  }

  JanusConstructor.randomString = (len) => 'stub' + String(len);
  JanusConstructor._instances = instances;

  return JanusConstructor;
}

// ═══════════════════════════════════════════════════════════════
// Sandbox setup
// ═══════════════════════════════════════════════════════════════
function createSandbox() {
  const clock = createFakeClock();
  const JanusStub = createJanusStub();

  // Minimal DOM stubs
  const elements = {};
  const eventListeners = {};

  function makeElement(id) {
    const el = {
      id,
      textContent: '',
      style: { display: '' },
      dataset: {},
      autoplay: false,
      playsInline: false,
      muted: false,
      srcObject: null,
      currentTime: 0,
      readyState: 0,
      addEventListener: (type, handler) => {
        if (!eventListeners[id]) eventListeners[id] = {};
        if (!eventListeners[id][type]) eventListeners[id][type] = [];
        eventListeners[id][type].push(handler);
      },
      setAttribute: () => {},
      play: async () => ({ ok: true, blocked: false }),
      requestVideoFrameCallback: undefined, // not available by default
      getTracks: () => [],
    };
    elements[id] = el;
    return el;
  }

  // Pre-create elements that DomUIAdapter needs
  makeElement('videoEl');
  makeElement('playBtn');
  makeElement('statsBtn');
  makeElement('statsBox');
  makeElement('statusPill');
  makeElement('debugPanel');

  const fakeDocument = {
    getElementById: (id) => elements[id] || null,
  };

  function FakeMediaStream() {
    const tracks = [];
    return {
      getTracks: () => [...tracks],
      addTrack: (t) => tracks.push(t),
      removeTrack: (t) => { const i = tracks.indexOf(t); if (i >= 0) tracks.splice(i, 1); },
    };
  }

  const sandbox = vm.createContext({
    window: {},
    console,
    Math: Math,
    Promise: Promise,
    setTimeout: clock.setTimeout,
    clearTimeout: clock.clearTimeout,
    document: fakeDocument,
    MediaStream: FakeMediaStream,
    Date: Date,
  });
  sandbox.Math.random = () => 0.5;
  sandbox.Janus = JanusStub;

  return { sandbox, clock, JanusStub, elements, eventListeners, FakeMediaStream };
}

async function main() {
  const root = path.resolve(__dirname, '..');

  // ═══════════════════════════════════════════════════════════
  // SECTION A: JanusSessionManager tests
  // ═══════════════════════════════════════════════════════════

  console.log('\n=== JanusSessionManager Tests ===\n');

  // --- SM Test 1: constructor defaults ---
  console.log('--- SM Test 1: constructor defaults ---');
  {
    const { sandbox, clock, JanusStub } = createSandbox();
    loadScript(sandbox, path.join(root, 'player', 'ns.js'));
    loadScript(sandbox, path.join(root, 'player', 'core', 'player_state.js'));
    loadScript(sandbox, path.join(root, 'player', 'core', 'codes.js'));
    loadScript(sandbox, path.join(root, 'player', 'adapters', 'logger.js'));
    loadScript(sandbox, path.join(root, 'player', 'adapters', 'janus_session_manager.js'));

    const AP = sandbox.window.AutonomousPlayer;
    const log = { debug: () => {}, info: () => {}, warn: () => {}, error: () => {} };
    const cfg = { janusWs: 'ws://localhost:8188', janusRest: null };
    const sm = new AP.Adapters.JanusSessionManager(cfg, log);

    assert(sm.janus === null, 'SM1: janus null initially');
    assert(sm.generation() === 0, 'SM1: generation starts at 0');
    assert(sm.isAlive() === false, 'SM1: not alive without session');
  }

  // --- SM Test 2: onEvent / _emit ---
  console.log('--- SM Test 2: onEvent / _emit ---');
  {
    const { sandbox, clock, JanusStub } = createSandbox();
    loadScript(sandbox, path.join(root, 'player', 'ns.js'));
    loadScript(sandbox, path.join(root, 'player', 'core', 'player_state.js'));
    loadScript(sandbox, path.join(root, 'player', 'core', 'codes.js'));
    loadScript(sandbox, path.join(root, 'player', 'adapters', 'logger.js'));
    loadScript(sandbox, path.join(root, 'player', 'adapters', 'janus_session_manager.js'));
    const AP = sandbox.window.AutonomousPlayer;
    const log = { debug: () => {}, info: () => {}, warn: () => {}, error: () => {} };
    const sm = new AP.Adapters.JanusSessionManager({ janusWs: 'ws://l' }, log);

    const events = [];
    const unsub = sm.onEvent((ev) => events.push(ev));
    sm._emit('SESSION_READY', { gen: 0 });
    assert(events.length === 1, 'SM2: event received');
    assert(events[0].type === 'SESSION_READY', 'SM2: event type');
    assert(events[0].payload.gen === 0, 'SM2: event payload');

    unsub();
    sm._emit('SESSION_READY', { gen: 1 });
    assert(events.length === 1, 'SM2: unsubscribed — no more events');
  }

  // --- SM Test 3: onEvent with bad callback ---
  console.log('--- SM Test 3: onEvent bad callback ---');
  {
    const { sandbox } = createSandbox();
    loadScript(sandbox, path.join(root, 'player', 'ns.js'));
    loadScript(sandbox, path.join(root, 'player', 'core', 'player_state.js'));
    loadScript(sandbox, path.join(root, 'player', 'core', 'codes.js'));
    loadScript(sandbox, path.join(root, 'player', 'adapters', 'logger.js'));
    loadScript(sandbox, path.join(root, 'player', 'adapters', 'janus_session_manager.js'));
    const AP = sandbox.window.AutonomousPlayer;
    const log = { debug: () => {}, info: () => {}, warn: () => {}, error: () => {} };
    const sm = new AP.Adapters.JanusSessionManager({ janusWs: 'ws://l' }, log);

    const unsub = sm.onEvent(null); // not a function
    assert(typeof unsub === 'function', 'SM3: returns noop unsub for null');
    unsub(); // should not throw
    passed++;
  }

  // --- SM Test 4: _emit swallows observer errors ---
  console.log('--- SM Test 4: _emit swallows errors ---');
  {
    const { sandbox } = createSandbox();
    loadScript(sandbox, path.join(root, 'player', 'ns.js'));
    loadScript(sandbox, path.join(root, 'player', 'core', 'player_state.js'));
    loadScript(sandbox, path.join(root, 'player', 'core', 'codes.js'));
    loadScript(sandbox, path.join(root, 'player', 'adapters', 'logger.js'));
    loadScript(sandbox, path.join(root, 'player', 'adapters', 'janus_session_manager.js'));
    const AP = sandbox.window.AutonomousPlayer;
    const log = { debug: () => {}, info: () => {}, warn: () => {}, error: () => {} };
    const sm = new AP.Adapters.JanusSessionManager({ janusWs: 'ws://l' }, log);

    const events2 = [];
    sm.onEvent(() => { throw new Error('boom'); });
    sm.onEvent((ev) => events2.push(ev));
    sm._emit('TEST', {});
    assert(events2.length === 1, 'SM4: second observer still called after first throws');
  }

  // --- SM Test 5: setRtcConfig stores config, null is ignored ---
  console.log('--- SM Test 5: setRtcConfig ---');
  {
    const { sandbox } = createSandbox();
    loadScript(sandbox, path.join(root, 'player', 'ns.js'));
    loadScript(sandbox, path.join(root, 'player', 'core', 'player_state.js'));
    loadScript(sandbox, path.join(root, 'player', 'core', 'codes.js'));
    loadScript(sandbox, path.join(root, 'player', 'adapters', 'logger.js'));
    loadScript(sandbox, path.join(root, 'player', 'adapters', 'janus_session_manager.js'));
    const AP = sandbox.window.AutonomousPlayer;
    const log = { debug: () => {}, info: () => {}, warn: () => {}, error: () => {} };
    const sm = new AP.Adapters.JanusSessionManager({ janusWs: 'ws://l' }, log);

    sm.setRtcConfig(null); // should not throw, should not set
    assert(sm._rtcConfig === null, 'SM5: null ignored');

    const rtc = { iceServers: [], iceTransportPolicy: 'all' };
    sm.setRtcConfig(rtc);
    assert(sm._rtcConfig === rtc, 'SM5: config stored');
  }

  // --- SM Test 6: init creates Janus session ---
  console.log('--- SM Test 6: init creates session ---');
  {
    const { sandbox, JanusStub } = createSandbox();
    loadScript(sandbox, path.join(root, 'player', 'ns.js'));
    loadScript(sandbox, path.join(root, 'player', 'core', 'player_state.js'));
    loadScript(sandbox, path.join(root, 'player', 'core', 'codes.js'));
    loadScript(sandbox, path.join(root, 'player', 'adapters', 'logger.js'));
    loadScript(sandbox, path.join(root, 'player', 'adapters', 'janus_session_manager.js'));
    const AP = sandbox.window.AutonomousPlayer;
    const log = { debug: () => {}, info: () => {}, warn: () => {}, error: () => {} };
    const sm = new AP.Adapters.JanusSessionManager({ janusWs: 'ws://localhost:8188' }, log);

    const events = [];
    sm.onEvent((ev) => events.push(ev));

    await sm.init({ iceServers: [], iceTransportPolicy: 'all' });
    assert(sm.janus !== null, 'SM6: janus created');
    assert(JanusStub._instances.length === 1, 'SM6: one Janus instance');
    const hasReady = events.some(e => e.type === 'SESSION_READY');
    assert(hasReady, 'SM6: SESSION_READY emitted');
  }

  // --- SM Test 7: init idempotent if already created ---
  console.log('--- SM Test 7: init idempotent ---');
  {
    const { sandbox, JanusStub } = createSandbox();
    loadScript(sandbox, path.join(root, 'player', 'ns.js'));
    loadScript(sandbox, path.join(root, 'player', 'core', 'player_state.js'));
    loadScript(sandbox, path.join(root, 'player', 'core', 'codes.js'));
    loadScript(sandbox, path.join(root, 'player', 'adapters', 'logger.js'));
    loadScript(sandbox, path.join(root, 'player', 'adapters', 'janus_session_manager.js'));
    const AP = sandbox.window.AutonomousPlayer;
    const log = { debug: () => {}, info: () => {}, warn: () => {}, error: () => {} };
    const sm = new AP.Adapters.JanusSessionManager({ janusWs: 'ws://l' }, log);

    await sm.init({ iceServers: [], iceTransportPolicy: 'all' });
    await sm.init(); // idempotent — should not create second
    assert(JanusStub._instances.length === 1, 'SM7: still only one Janus instance');
  }

  // --- SM Test 8: init without rtcConfig throws ---
  console.log('--- SM Test 8: init without rtcConfig ---');
  {
    const { sandbox } = createSandbox();
    loadScript(sandbox, path.join(root, 'player', 'ns.js'));
    loadScript(sandbox, path.join(root, 'player', 'core', 'player_state.js'));
    loadScript(sandbox, path.join(root, 'player', 'core', 'codes.js'));
    loadScript(sandbox, path.join(root, 'player', 'adapters', 'logger.js'));
    loadScript(sandbox, path.join(root, 'player', 'adapters', 'janus_session_manager.js'));
    const AP = sandbox.window.AutonomousPlayer;
    const log = { debug: () => {}, info: () => {}, warn: () => {}, error: () => {} };
    const sm = new AP.Adapters.JanusSessionManager({ janusWs: 'ws://l' }, log);

    let threw = false;
    try { await sm.init(); } catch (e) { threw = true; }
    assert(threw, 'SM8: init without rtcConfig throws');
  }

  // --- SM Test 9: isAlive delegates to janus.isConnected ---
  console.log('--- SM Test 9: isAlive ---');
  {
    const { sandbox, JanusStub } = createSandbox();
    loadScript(sandbox, path.join(root, 'player', 'ns.js'));
    loadScript(sandbox, path.join(root, 'player', 'core', 'player_state.js'));
    loadScript(sandbox, path.join(root, 'player', 'core', 'codes.js'));
    loadScript(sandbox, path.join(root, 'player', 'adapters', 'logger.js'));
    loadScript(sandbox, path.join(root, 'player', 'adapters', 'janus_session_manager.js'));
    const AP = sandbox.window.AutonomousPlayer;
    const log = { debug: () => {}, info: () => {}, warn: () => {}, error: () => {} };
    const sm = new AP.Adapters.JanusSessionManager({ janusWs: 'ws://l' }, log);

    assert(sm.isAlive() === false, 'SM9: not alive without session');
    await sm.init({ iceServers: [], iceTransportPolicy: 'all' });
    assert(sm.isAlive() === true, 'SM9: alive after init');

    // Simulate disconnect
    JanusStub._instances[0]._connected = false;
    assert(sm.isAlive() === false, 'SM9: not alive when disconnected');
  }

  // --- SM Test 10: destroy increments generation ---
  console.log('--- SM Test 10: destroy ---');
  {
    const { sandbox, JanusStub } = createSandbox();
    loadScript(sandbox, path.join(root, 'player', 'ns.js'));
    loadScript(sandbox, path.join(root, 'player', 'core', 'player_state.js'));
    loadScript(sandbox, path.join(root, 'player', 'core', 'codes.js'));
    loadScript(sandbox, path.join(root, 'player', 'adapters', 'logger.js'));
    loadScript(sandbox, path.join(root, 'player', 'adapters', 'janus_session_manager.js'));
    const AP = sandbox.window.AutonomousPlayer;
    const log = { debug: () => {}, info: () => {}, warn: () => {}, error: () => {} };
    const sm = new AP.Adapters.JanusSessionManager({ janusWs: 'ws://l' }, log);

    await sm.init({ iceServers: [], iceTransportPolicy: 'all' });
    assert(sm.generation() === 0, 'SM10: gen 0 after init');

    const events = [];
    sm.onEvent((ev) => events.push(ev));
    await sm.destroy();
    assert(sm.janus === null, 'SM10: janus null after destroy');
    assert(sm.generation() === 1, 'SM10: gen 1 after destroy');
    assert(events.some(e => e.type === 'SESSION_DESTROYED'), 'SM10: SESSION_DESTROYED emitted');
  }

  // --- SM Test 11: destroy without session still emits ---
  console.log('--- SM Test 11: destroy without session ---');
  {
    const { sandbox } = createSandbox();
    loadScript(sandbox, path.join(root, 'player', 'ns.js'));
    loadScript(sandbox, path.join(root, 'player', 'core', 'player_state.js'));
    loadScript(sandbox, path.join(root, 'player', 'core', 'codes.js'));
    loadScript(sandbox, path.join(root, 'player', 'adapters', 'logger.js'));
    loadScript(sandbox, path.join(root, 'player', 'adapters', 'janus_session_manager.js'));
    const AP = sandbox.window.AutonomousPlayer;
    const log = { debug: () => {}, info: () => {}, warn: () => {}, error: () => {} };
    const sm = new AP.Adapters.JanusSessionManager({ janusWs: 'ws://l' }, log);

    const events = [];
    sm.onEvent((ev) => events.push(ev));
    await sm.destroy();
    assert(events.some(e => e.type === 'SESSION_DESTROYED'), 'SM11: emits even without session');
  }

  // --- SM Test 12: attach delegates to janus.attach ---
  console.log('--- SM Test 12: attach ---');
  {
    const { sandbox, JanusStub } = createSandbox();
    loadScript(sandbox, path.join(root, 'player', 'ns.js'));
    loadScript(sandbox, path.join(root, 'player', 'core', 'player_state.js'));
    loadScript(sandbox, path.join(root, 'player', 'core', 'codes.js'));
    loadScript(sandbox, path.join(root, 'player', 'adapters', 'logger.js'));
    loadScript(sandbox, path.join(root, 'player', 'adapters', 'janus_session_manager.js'));
    const AP = sandbox.window.AutonomousPlayer;
    const log = { debug: () => {}, info: () => {}, warn: () => {}, error: () => {} };
    const sm = new AP.Adapters.JanusSessionManager({ janusWs: 'ws://l' }, log);

    await sm.init({ iceServers: [], iceTransportPolicy: 'all' });
    const handle = await sm.attach('janus.plugin.streaming', { opaqueId: 'test' });
    assert(handle != null, 'SM12: handle returned');
    assert(handle.plugin === 'janus.plugin.streaming', 'SM12: correct plugin');
  }

  // --- SM Test 13: recreate destroys and re-inits ---
  console.log('--- SM Test 13: recreate ---');
  {
    const { sandbox, JanusStub } = createSandbox();
    loadScript(sandbox, path.join(root, 'player', 'ns.js'));
    loadScript(sandbox, path.join(root, 'player', 'core', 'player_state.js'));
    loadScript(sandbox, path.join(root, 'player', 'core', 'codes.js'));
    loadScript(sandbox, path.join(root, 'player', 'adapters', 'logger.js'));
    loadScript(sandbox, path.join(root, 'player', 'adapters', 'janus_session_manager.js'));
    const AP = sandbox.window.AutonomousPlayer;
    const log = { debug: () => {}, info: () => {}, warn: () => {}, error: () => {} };
    const sm = new AP.Adapters.JanusSessionManager({ janusWs: 'ws://l' }, log);

    await sm.init({ iceServers: [], iceTransportPolicy: 'all' });
    const events = [];
    sm.onEvent((ev) => events.push(ev));

    await sm.recreate({ iceServers: [{ urls: 'stun:s' }], iceTransportPolicy: 'relay' });
    assert(sm.janus !== null, 'SM13: janus exists after recreate');
    assert(sm.generation() === 1, 'SM13: gen incremented');
    assert(events.some(e => e.type === 'SESSION_DESTROYED'), 'SM13: SESSION_DESTROYED emitted');
    assert(events.some(e => e.type === 'SESSION_RECREATED'), 'SM13: SESSION_RECREATED emitted');
  }

  // ═══════════════════════════════════════════════════════════
  // SECTION B: JanusStreamingAdapter tests
  // ═══════════════════════════════════════════════════════════

  console.log('\n=== JanusStreamingAdapter Tests ===\n');

  // Helper: create SM + streaming adapter in same sandbox
  function createStreamingSetup(overrideCfg) {
    const { sandbox, clock, JanusStub, FakeMediaStream } = createSandbox();
    loadScript(sandbox, path.join(root, 'player', 'ns.js'));
    loadScript(sandbox, path.join(root, 'player', 'core', 'player_state.js'));
    loadScript(sandbox, path.join(root, 'player', 'core', 'codes.js'));
    loadScript(sandbox, path.join(root, 'player', 'adapters', 'logger.js'));
    loadScript(sandbox, path.join(root, 'player', 'adapters', 'janus_session_manager.js'));
    loadScript(sandbox, path.join(root, 'player', 'adapters', 'janus_streaming_adapter.js'));

    const AP = sandbox.window.AutonomousPlayer;
    const log = { debug: () => {}, info: () => {}, warn: () => {}, error: () => {} };
    const cfg = Object.assign({ janusWs: 'ws://l', janusRest: null }, overrideCfg || {});
    const sm = new AP.Adapters.JanusSessionManager(cfg, log);
    const sa = new AP.Adapters.JanusStreamingAdapter(cfg, log, sm, clock);

    return { AP, sm, sa, clock, JanusStub, log };
  }

  // --- SA Test 1: constructor requires sessionManager ---
  console.log('--- SA Test 1: constructor requires session ---');
  {
    const { sandbox, clock } = createSandbox();
    loadScript(sandbox, path.join(root, 'player', 'ns.js'));
    loadScript(sandbox, path.join(root, 'player', 'core', 'player_state.js'));
    loadScript(sandbox, path.join(root, 'player', 'core', 'codes.js'));
    loadScript(sandbox, path.join(root, 'player', 'adapters', 'logger.js'));
    loadScript(sandbox, path.join(root, 'player', 'adapters', 'janus_session_manager.js'));
    loadScript(sandbox, path.join(root, 'player', 'adapters', 'janus_streaming_adapter.js'));
    const AP = sandbox.window.AutonomousPlayer;
    const log = { debug: () => {}, info: () => {}, warn: () => {}, error: () => {} };

    let threw = false;
    try { new AP.Adapters.JanusStreamingAdapter({}, log, null, clock); } catch (e) { threw = true; }
    assert(threw, 'SA1: throws without sessionManager');
  }

  // --- SA Test 2: init creates session and attaches handle ---
  console.log('--- SA Test 2: init attaches handle ---');
  {
    const { sa, sm } = createStreamingSetup();
    const events = [];
    sa.setEventSink((ev) => events.push(ev), () => 1);

    await sa.init({ iceServers: [], iceTransportPolicy: 'all' });
    assert(sa.handle !== null, 'SA2: handle attached');
    assert(events.some(e => e.type === 'HANDLE_ATTACHED'), 'SA2: HANDLE_ATTACHED emitted');
  }

  // --- SA Test 3: getInboundStream returns MediaStream ---
  console.log('--- SA Test 3: getInboundStream ---');
  {
    const { sa, sm } = createStreamingSetup();
    const stream = sa.getInboundStream();
    assert(stream !== null && typeof stream.getTracks === 'function', 'SA3: returns MediaStream-like');
  }

  // --- SA Test 4: getPeerConnection returns null before init ---
  console.log('--- SA Test 4: getPeerConnection ---');
  {
    const { sa } = createStreamingSetup();
    assert(sa.getPeerConnection() === null, 'SA4: null before init');

    await sa.init({ iceServers: [], iceTransportPolicy: 'all' });
    const pc = sa.getPeerConnection();
    assert(pc !== null, 'SA4: pc available after init');
    assert(pc.mock === 'peerConnection', 'SA4: correct mock pc');
  }

  // --- SA Test 5: isSessionAlive delegates ---
  console.log('--- SA Test 5: isSessionAlive ---');
  {
    const { sa, sm, JanusStub } = createStreamingSetup();
    assert(sa.isSessionAlive() === false, 'SA5: not alive before init');

    await sa.init({ iceServers: [], iceTransportPolicy: 'all' });
    assert(sa.isSessionAlive() === true, 'SA5: alive after init');
  }

  // --- SA Test 6: _dropIfStaleGen filters stale callbacks ---
  console.log('--- SA Test 6: _dropIfStaleGen ---');
  {
    const { sa } = createStreamingSetup();
    assert(sa._dropIfStaleGen(0, 'test') === false, 'SA6: gen 0 is current');
    assert(sa._dropIfStaleGen(99, 'test') === true, 'SA6: gen 99 is stale');
  }

  // --- SA Test 7: session reset invalidates handle ---
  console.log('--- SA Test 7: session reset ---');
  {
    const { sa, sm } = createStreamingSetup();
    await sa.init({ iceServers: [], iceTransportPolicy: 'all' });
    assert(sa.handle !== null, 'SA7: handle before reset');

    const events = [];
    sa.setEventSink((ev) => events.push(ev), () => 1);

    // Simulate session destroy which triggers the onEvent listener
    await sm.destroy();
    assert(sa.handle === null, 'SA7: handle null after session destroy');
    assert(events.some(e => e.type === 'SESSION_RESET'), 'SA7: SESSION_RESET emitted');
  }

  // --- SA Test 8: _emit with and without token ---
  console.log('--- SA Test 8: _emit with token ---');
  {
    const { sa } = createStreamingSetup();
    const events = [];
    sa.setEventSink((ev) => events.push(ev), () => 42);
    sa._emit('TEST', { x: 1 });
    assert(events.length === 1, 'SA8: event emitted');
    assert(events[0].token === 42, 'SA8: token attached');
    assert(events[0].type === 'TEST', 'SA8: type correct');
  }

  // --- SA Test 9: _enqueue serializes operations ---
  console.log('--- SA Test 9: _enqueue serialization ---');
  {
    const { sa } = createStreamingSetup();
    const order = [];
    await sa._enqueue(async () => { order.push(1); });
    await sa._enqueue(async () => { order.push(2); });
    assert(order.length === 2, 'SA9: both ran');
    assert(order[0] === 1 && order[1] === 2, 'SA9: in order');
  }

  // --- SA Test 10: _enqueue swallows errors ---
  console.log('--- SA Test 10: _enqueue error handling ---');
  {
    const { sa } = createStreamingSetup();
    const order = [];
    await sa._enqueue(async () => { throw new Error('fail'); });
    await sa._enqueue(async () => { order.push('after-error'); });
    assert(order[0] === 'after-error', 'SA10: chain continues after error');
  }

  // --- SA Test 11: ensureReady throws if no handle ---
  console.log('--- SA Test 11: ensureReady ---');
  {
    const { sa } = createStreamingSetup();
    let threw = false;
    try { await sa.ensureReady(); } catch (e) { threw = true; }
    assert(threw, 'SA11: throws without handle');
  }

  // --- SA Test 12: watch sends correct message ---
  console.log('--- SA Test 12: watch ---');
  {
    const { sa } = createStreamingSetup();
    await sa.init({ iceServers: [], iceTransportPolicy: 'all' });

    // watch with valid streamId. The enqueue'd async fn hasn't started yet,
    // so we need to yield to let it run before triggering onmessage.
    const watchPromise = sa.watch(1);
    await Promise.resolve(); // let enqueued fn start → _pendingWatch is set

    // Get the handle and trigger onmessage
    const h = sa.handle;
    const onmessage = h.callbacks.onmessage;
    onmessage({ result: { status: 'preparing' } }, null); // substantive response without jsep

    const result = await watchPromise;
    assert(result === true, 'SA12: watch resolved');
  }

  // --- SA Test 13: stop sends stop request ---
  console.log('--- SA Test 13: stop ---');
  {
    const { sa } = createStreamingSetup();
    await sa.init({ iceServers: [], iceTransportPolicy: 'all' });

    // Trigger cleanup resolve manually
    if (sa._cleanupResolve) sa._cleanupResolve();

    await sa.stop();
    // Should not throw
    passed++;
  }

  // --- SA Test 14: stop no-op without handle ---
  console.log('--- SA Test 14: stop without handle ---');
  {
    const { sa } = createStreamingSetup();
    await sa.stop(); // no handle → returns resolved
    passed++;
  }

  // --- SA Test 15: onremotetrack adds/removes tracks ---
  console.log('--- SA Test 15: onremotetrack ---');
  {
    const { sa } = createStreamingSetup();
    await sa.init({ iceServers: [], iceTransportPolicy: 'all' });

    const events = [];
    sa.setEventSink((ev) => events.push(ev), () => 1);

    const h = sa.handle;
    const onremotetrack = h.callbacks.onremotetrack;

    const fakeTrack = { id: 't1', kind: 'video', onmute: null, onunmute: null, onended: null };
    onremotetrack(fakeTrack, '0', true);
    assert(events.some(e => e.type === 'TRACK' && e.payload.on === true), 'SA15: TRACK on=true emitted');

    const stream = sa.getInboundStream();
    assert(stream.getTracks().length === 1, 'SA15: track added to stream');

    // Remove track
    onremotetrack(fakeTrack, '0', false);
    assert(events.some(e => e.type === 'TRACK' && e.payload.on === false), 'SA15: TRACK on=false emitted');
    assert(stream.getTracks().length === 0, 'SA15: track removed from stream');
  }

  // --- SA Test 16: track events (mute/unmute/ended) ---
  console.log('--- SA Test 16: track events ---');
  {
    const { sa } = createStreamingSetup();
    await sa.init({ iceServers: [], iceTransportPolicy: 'all' });

    const events = [];
    sa.setEventSink((ev) => events.push(ev), () => 1);

    const h = sa.handle;
    const onremotetrack = h.callbacks.onremotetrack;

    const fakeTrack = { id: 't1', kind: 'video', onmute: null, onunmute: null, onended: null };
    onremotetrack(fakeTrack, '0', true);

    // Trigger mute/unmute/ended
    fakeTrack.onmute();
    assert(events.some(e => e.type === 'TRACK_MUTED'), 'SA16: TRACK_MUTED emitted');

    fakeTrack.onunmute();
    assert(events.some(e => e.type === 'TRACK_UNMUTED'), 'SA16: TRACK_UNMUTED emitted');

    fakeTrack.onended();
    assert(events.some(e => e.type === 'TRACK_ENDED'), 'SA16: TRACK_ENDED emitted');
  }

  // --- SA Test 17: webrtcState callback ---
  console.log('--- SA Test 17: webrtcState ---');
  {
    const { sa } = createStreamingSetup();
    await sa.init({ iceServers: [], iceTransportPolicy: 'all' });

    const events = [];
    sa.setEventSink((ev) => events.push(ev), () => 1);

    const h = sa.handle;
    h.callbacks.webrtcState(true, 'connected');
    assert(events.some(e => e.type === 'WEBRTC_STATE' && e.payload.up === true), 'SA17: WEBRTC_STATE up');

    h.callbacks.webrtcState(false, 'failed');
    assert(events.some(e => e.type === 'WEBRTC_STATE' && e.payload.up === false), 'SA17: WEBRTC_STATE down');
  }

  // --- SA Test 18: iceState callback ---
  console.log('--- SA Test 18: iceState ---');
  {
    const { sa } = createStreamingSetup();
    await sa.init({ iceServers: [], iceTransportPolicy: 'all' });

    const events = [];
    sa.setEventSink((ev) => events.push(ev), () => 1);

    sa.handle.callbacks.iceState('connected');
    assert(events.some(e => e.type === 'ICE_STATE' && e.payload.state === 'connected'), 'SA18: ICE_STATE emitted');
  }

  // --- SA Test 19: hangup callback ---
  console.log('--- SA Test 19: hangup ---');
  {
    const { sa } = createStreamingSetup();
    await sa.init({ iceServers: [], iceTransportPolicy: 'all' });

    const events = [];
    sa.setEventSink((ev) => events.push(ev), () => 1);

    sa.handle.callbacks.hangup('ICE failed');
    assert(events.some(e => e.type === 'HANGUP' && e.payload.reason === 'ICE failed'), 'SA19: HANGUP emitted');
  }

  // --- SA Test 20: slowLink callback ---
  console.log('--- SA Test 20: slowLink ---');
  {
    const { sa } = createStreamingSetup();
    await sa.init({ iceServers: [], iceTransportPolicy: 'all' });

    const events = [];
    sa.setEventSink((ev) => events.push(ev), () => 1);

    sa.handle.callbacks.slowLink(true, 5, '0');
    assert(events.some(e => e.type === 'SLOW_LINK' && e.payload.uplink === true && e.payload.lost === 5), 'SA20: SLOW_LINK emitted');
  }

  // --- SA Test 21: recreate flushes IO and bumps ioGen ---
  console.log('--- SA Test 21: recreate ---');
  {
    const { sa, sm } = createStreamingSetup();
    await sa.init({ iceServers: [], iceTransportPolicy: 'all' });
    const oldGen = sa._ioGen;

    await sa.recreate({ iceServers: [], iceTransportPolicy: 'all' });
    assert(sa._ioGen > oldGen, 'SA21: ioGen bumped');
    assert(sa.handle !== null, 'SA21: handle re-attached after recreate');
  }

  // ═══════════════════════════════════════════════════════════
  // SECTION C: JanusTextRoomAdapter tests
  // ═══════════════════════════════════════════════════════════

  console.log('\n=== JanusTextRoomAdapter Tests ===\n');

  function createTextRoomSetup() {
    const { sandbox, clock, JanusStub } = createSandbox();
    loadScript(sandbox, path.join(root, 'player', 'ns.js'));
    loadScript(sandbox, path.join(root, 'player', 'core', 'player_state.js'));
    loadScript(sandbox, path.join(root, 'player', 'core', 'codes.js'));
    loadScript(sandbox, path.join(root, 'player', 'adapters', 'logger.js'));
    loadScript(sandbox, path.join(root, 'player', 'adapters', 'janus_session_manager.js'));
    loadScript(sandbox, path.join(root, 'player', 'adapters', 'janus_textroom_adapter.js'));

    const AP = sandbox.window.AutonomousPlayer;
    const log = { debug: () => {}, info: () => {}, warn: () => {}, error: () => {} };
    const cfg = { janusWs: 'ws://l', debug: false };
    const sm = new AP.Adapters.JanusSessionManager(cfg, log);
    const tr = new AP.Adapters.JanusTextRoomAdapter(cfg, log, sm);

    return { AP, sm, tr, clock, JanusStub, log };
  }

  // --- TR Test 1: constructor requires sessionManager ---
  console.log('--- TR Test 1: constructor requires session ---');
  {
    const { sandbox } = createSandbox();
    loadScript(sandbox, path.join(root, 'player', 'ns.js'));
    loadScript(sandbox, path.join(root, 'player', 'core', 'player_state.js'));
    loadScript(sandbox, path.join(root, 'player', 'core', 'codes.js'));
    loadScript(sandbox, path.join(root, 'player', 'adapters', 'logger.js'));
    loadScript(sandbox, path.join(root, 'player', 'adapters', 'janus_session_manager.js'));
    loadScript(sandbox, path.join(root, 'player', 'adapters', 'janus_textroom_adapter.js'));
    const AP = sandbox.window.AutonomousPlayer;
    const log = { debug: () => {}, info: () => {}, warn: () => {}, error: () => {} };

    let threw = false;
    try { new AP.Adapters.JanusTextRoomAdapter({}, log, null); } catch(e) { threw = true; }
    assert(threw, 'TR1: requires sessionManager');
  }

  // --- TR Test 2: attach creates handle + sends setup ---
  console.log('--- TR Test 2: attach ---');
  {
    const { tr, sm, JanusStub } = createTextRoomSetup();
    sm.setRtcConfig({ iceServers: [], iceTransportPolicy: 'all' });

    await tr.attach();
    assert(tr.handle !== null, 'TR2: handle attached');
    assert(tr.handle.plugin === 'janus.plugin.textroom', 'TR2: correct plugin');
  }

  // --- TR Test 3: ondataopen sets ready, joins room ---
  console.log('--- TR Test 3: ondataopen ---');
  {
    const { tr, sm } = createTextRoomSetup();
    sm.setRtcConfig({ iceServers: [], iceTransportPolicy: 'all' });
    await tr.attach();

    assert(tr.ready === false, 'TR3: not ready before ondataopen');
    tr.handle.callbacks.ondataopen();
    assert(tr.ready === true, 'TR3: ready after ondataopen');
    assert(tr.handle._lastData !== null, 'TR3: join sent');

    const joinPayload = JSON.parse(tr.handle._lastData.text);
    assert(joinPayload.textroom === 'join', 'TR3: join command');
    assert(joinPayload.room === 1000, 'TR3: room 1000');
  }

  // --- TR Test 4: sendFrame only when ready ---
  console.log('--- TR Test 4: sendFrame ---');
  {
    const { tr, sm } = createTextRoomSetup();
    sm.setRtcConfig({ iceServers: [], iceTransportPolicy: 'all' });
    await tr.attach();

    // Not ready — should be no-op
    tr.sendFrame({ axes: [0, 0] });
    const dataBefore = tr.handle._lastData;

    // Make ready
    tr.handle.callbacks.ondataopen();
    tr.sendFrame({ axes: [1, 0] });

    const sentData = tr.handle._lastData;
    assert(sentData !== null, 'TR4: data sent');
    const envelope = JSON.parse(sentData.text);
    assert(envelope.textroom === 'message', 'TR4: message envelope');
    assert(envelope.room === 1000, 'TR4: room 1000');
    const frame = JSON.parse(envelope.text);
    assert(frame.axes[0] === 1, 'TR4: frame data correct');
  }

  // --- TR Test 5: sendPing only when ready ---
  console.log('--- TR Test 5: sendPing ---');
  {
    const { tr, sm } = createTextRoomSetup();
    sm.setRtcConfig({ iceServers: [], iceTransportPolicy: 'all' });
    await tr.attach();

    tr.sendPing(42);
    // Not ready — no data sent beyond _ from ondataopen
    tr.handle.callbacks.ondataopen();
    tr.sendPing(42);

    const envelope = JSON.parse(tr.handle._lastData.text);
    const ping = JSON.parse(envelope.text);
    assert(ping.type === 'ping', 'TR5: ping type');
    assert(ping.id === 42, 'TR5: ping id');
  }

  // --- TR Test 6: session reset invalidates handle ---
  console.log('--- TR Test 6: session reset ---');
  {
    const { tr, sm } = createTextRoomSetup();
    sm.setRtcConfig({ iceServers: [], iceTransportPolicy: 'all' });
    await tr.attach();
    tr.handle.callbacks.ondataopen();
    assert(tr.ready === true, 'TR6: ready before reset');

    await sm.destroy();
    assert(tr.handle === null, 'TR6: handle null after session destroy');
    assert(tr.ready === false, 'TR6: not ready after session destroy');
  }

  // --- TR Test 7: detach cleans up ---
  console.log('--- TR Test 7: detach ---');
  {
    const { tr, sm } = createTextRoomSetup();
    sm.setRtcConfig({ iceServers: [], iceTransportPolicy: 'all' });
    await tr.attach();
    assert(tr.handle !== null, 'TR7: handle before detach');

    await tr.detach();
    assert(tr.handle === null, 'TR7: handle null after detach');
    assert(tr.ready === false, 'TR7: not ready after detach');
  }

  // --- TR Test 8: detach without handle is no-op ---
  console.log('--- TR Test 8: detach no-op ---');
  {
    const { tr } = createTextRoomSetup();
    await tr.detach(); // no handle → no-op
    passed++;
  }

  // --- TR Test 9: oncleanup resets state ---
  console.log('--- TR Test 9: oncleanup ---');
  {
    const { tr, sm } = createTextRoomSetup();
    sm.setRtcConfig({ iceServers: [], iceTransportPolicy: 'all' });
    await tr.attach();
    tr.handle.callbacks.ondataopen();
    assert(tr.ready === true, 'TR9: ready before cleanup');

    const savedCallbacks = tr.handle.callbacks;
    savedCallbacks.oncleanup();
    assert(tr.handle === null, 'TR9: handle null after oncleanup');
    assert(tr.ready === false, 'TR9: not ready after oncleanup');
  }

  // ═══════════════════════════════════════════════════════════
  // SECTION D: DomUIAdapter tests
  // ═══════════════════════════════════════════════════════════

  console.log('\n=== DomUIAdapter Tests ===\n');

  function createDomSetup(overrides) {
    const { sandbox, clock, elements, eventListeners, JanusStub } = createSandbox();
    loadScript(sandbox, path.join(root, 'player', 'ns.js'));
    loadScript(sandbox, path.join(root, 'player', 'core', 'player_state.js'));
    loadScript(sandbox, path.join(root, 'player', 'core', 'codes.js'));
    loadScript(sandbox, path.join(root, 'player', 'adapters', 'logger.js'));
    loadScript(sandbox, path.join(root, 'player', 'adapters', 'dom_ui_adapter.js'));

    const AP = sandbox.window.AutonomousPlayer;
    const log = { debug: () => {}, info: () => {}, warn: () => {}, error: () => {} };
    const cfg = Object.assign({
      videoId: 'videoEl',
      playButtonId: 'playBtn',
      statsButtonId: 'statsBtn',
      statsBoxId: 'statsBox',
      statusPillId: 'statusPill',
      debugPanelId: 'debugPanel',
      autoplayEnabled: true,
      debugPanelEnabled: true,
    }, overrides || {});

    const ui = new AP.Adapters.DomUIAdapter(cfg, log, clock);
    return { AP, ui, clock, elements, eventListeners, log };
  }

  // --- DOM Test 1: constructor sets video attributes ---
  console.log('--- DOM Test 1: constructor video setup ---');
  {
    const { elements } = createDomSetup();
    assert(elements.videoEl.autoplay === true, 'DOM1: autoplay');
    assert(elements.videoEl.playsInline === true, 'DOM1: playsInline');
    assert(elements.videoEl.muted === true, 'DOM1: muted');
  }

  // --- DOM Test 2: constructor throws for missing elements ---
  console.log('--- DOM Test 2: missing element ---');
  {
    const { sandbox, clock } = createSandbox();
    loadScript(sandbox, path.join(root, 'player', 'ns.js'));
    loadScript(sandbox, path.join(root, 'player', 'core', 'player_state.js'));
    loadScript(sandbox, path.join(root, 'player', 'core', 'codes.js'));
    loadScript(sandbox, path.join(root, 'player', 'adapters', 'logger.js'));
    loadScript(sandbox, path.join(root, 'player', 'adapters', 'dom_ui_adapter.js'));
    const AP = sandbox.window.AutonomousPlayer;
    const log = { debug: () => {}, info: () => {}, warn: () => {}, error: () => {} };

    let threw = false;
    try {
      new AP.Adapters.DomUIAdapter({
        videoId: 'nonexistent',
        playButtonId: 'playBtn',
        statsButtonId: 'statsBtn',
        statsBoxId: 'statsBox',
        statusPillId: 'statusPill',
        debugPanelId: 'debugPanel',
      }, log, clock);
    } catch (e) { threw = true; }
    assert(threw, 'DOM2: throws for missing #videoEl');
  }

  // --- DOM Test 3: bindIntents wires play/retry handlers ---
  console.log('--- DOM Test 3: bindIntents ---');
  {
    const { ui, eventListeners } = createDomSetup();
    let toggled = false;
    let retried = false;
    ui.bindIntents({ onTogglePlay: () => { toggled = true; }, onRetry: () => { retried = true; } });

    // Click play button — mode is empty → onTogglePlay
    const playClicks = eventListeners.playBtn && eventListeners.playBtn.click;
    assert(playClicks && playClicks.length >= 1, 'DOM3: click handler registered');
    playClicks[0]();
    assert(toggled, 'DOM3: onTogglePlay called');

    // Switch to retry mode
    ui.playBtn.dataset.mode = 'retry';
    playClicks[0]();
    assert(retried, 'DOM3: onRetry called');
  }

  // --- DOM Test 4: setStatsText ---
  console.log('--- DOM Test 4: setStatsText ---');
  {
    const { ui, elements } = createDomSetup();
    ui.setStatsText('FPS: 30');
    assert(elements.statsBox.textContent === 'FPS: 30', 'DOM4: stats text set');
    ui.setStatsText(null);
    assert(elements.statsBox.textContent === '', 'DOM4: null clears text');
  }

  // --- DOM Test 5: setDebugText / hideDebug ---
  console.log('--- DOM Test 5: setDebugText ---');
  {
    const { ui, elements } = createDomSetup();
    ui.setDebugText('debug info');
    assert(elements.debugPanel.textContent === 'debug info', 'DOM5: debug text');
    assert(elements.debugPanel.style.display === 'block', 'DOM5: debug visible');

    ui.hideDebug();
    assert(elements.debugPanel.style.display === 'none', 'DOM5: debug hidden');
  }

  // --- DOM Test 6: setDebugText skips when disabled ---
  console.log('--- DOM Test 6: debug disabled ---');
  {
    const { ui, elements } = createDomSetup({ debugPanelEnabled: false });
    ui.setDebugText('should not appear');
    assert(elements.debugPanel.textContent !== 'should not appear', 'DOM6: debugText skipped when disabled');
  }

  // --- DOM Test 7: render sets status pill ---
  console.log('--- DOM Test 7: render ---');
  {
    const { ui, elements, AP } = createDomSetup();
    const PS = AP.Core.PlayerState;

    ui.render({ state: PS.PLAYING, attempt: 0, desiredPlaying: true });
    assert(elements.statusPill.dataset.state === PS.PLAYING, 'DOM7: pill state PLAYING');
    assert(elements.statusPill.style.display === 'flex', 'DOM7: pill visible');
  }

  // --- DOM Test 8: render shows retry button on ERROR ---
  console.log('--- DOM Test 8: render ERROR ---');
  {
    const { ui, elements, AP } = createDomSetup();
    const PS = AP.Core.PlayerState;

    ui.render({ state: PS.ERROR, attempt: 0, desiredPlaying: true });
    assert(elements.playBtn.style.display === 'block', 'DOM8: retry button visible');
    assert(elements.playBtn.textContent === 'Retry', 'DOM8: button says Retry');
    assert(elements.playBtn.dataset.mode === 'retry', 'DOM8: mode is retry');
  }

  // --- DOM Test 9: render hides button in normal autoplay ---
  console.log('--- DOM Test 9: render normal autoplay ---');
  {
    const { ui, elements, AP } = createDomSetup();
    const PS = AP.Core.PlayerState;

    ui.render({ state: PS.PLAYING, attempt: 0, desiredPlaying: true });
    assert(elements.playBtn.style.display === 'none', 'DOM9: play button hidden in autoplay+PLAYING');
  }

  // --- DOM Test 10: render degraded state ---
  console.log('--- DOM Test 10: render degraded ---');
  {
    const { ui, elements, AP } = createDomSetup();
    ui.render({ state: 'PLAYING', attempt: 0, desiredPlaying: true, degraded: true });
    assert(elements.statusPill.dataset.state === 'DEGRADED', 'DOM10: pill shows DEGRADED');
  }

  // --- DOM Test 11: render non-autoplay mode ---
  console.log('--- DOM Test 11: render non-autoplay ---');
  {
    const { ui, elements, AP } = createDomSetup({ autoplayEnabled: false });
    const PS = AP.Core.PlayerState;

    ui.render({ state: PS.IDLE, attempt: 0, desiredPlaying: false });
    assert(elements.playBtn.textContent === 'Play', 'DOM11: button says Play');
    assert(elements.playBtn.style.display === 'block', 'DOM11: button visible');

    ui.render({ state: PS.IDLE, attempt: 0, desiredPlaying: true });
    assert(elements.playBtn.textContent === 'Stop', 'DOM11: button says Stop when desired');
  }

  // --- DOM Test 12: bindStream sets srcObject ---
  console.log('--- DOM Test 12: bindStream ---');
  {
    const { ui, elements } = createDomSetup();
    const fakeStream = { getTracks: () => [] };
    ui.bindStream(fakeStream);
    assert(elements.videoEl.srcObject === fakeStream, 'DOM12: srcObject set');
  }

  // --- DOM Test 13: bindStream clears previous ---
  console.log('--- DOM Test 13: bindStream clears previous ---');
  {
    const { ui, elements } = createDomSetup();
    const s1 = { getTracks: () => [] };
    const s2 = { getTracks: () => [] };
    ui.bindStream(s1);
    ui.bindStream(s2);
    assert(elements.videoEl.srcObject === s2, 'DOM13: srcObject updated to new stream');
  }

  // --- DOM Test 14: onVideoStalled debounced ---
  console.log('--- DOM Test 14: onVideoStalled debounce ---');
  {
    const { ui, clock, eventListeners } = createDomSetup();
    const stallEvents = [];
    ui.onVideoStalled((name) => stallEvents.push(name));

    // Fire stalled event
    const stalledHandlers = eventListeners.videoEl && eventListeners.videoEl.stalled;
    assert(stalledHandlers && stalledHandlers.length >= 1, 'DOM14: stalled handler registered');
    stalledHandlers[0]();
    assert(stallEvents.length === 0, 'DOM14: not fired yet (debounced)');

    clock.advance(500);
    assert(stallEvents.length === 1, 'DOM14: fired after 500ms');
    assert(stallEvents[0] === 'stalled', 'DOM14: event name');
  }

  // --- DOM Test 15: video stall debounce coalesces ---
  console.log('--- DOM Test 15: stall debounce coalesces ---');
  {
    const { ui, clock, eventListeners } = createDomSetup();
    const stallEvents = [];
    ui.onVideoStalled((name) => stallEvents.push(name));

    const stalledHandlers = eventListeners.videoEl.stalled;
    const waitingHandlers = eventListeners.videoEl.waiting;

    stalledHandlers[0](); // first stalled
    waitingHandlers[0](); // waiting during debounce → ignored (debounce already armed)

    clock.advance(500);
    assert(stallEvents.length === 1, 'DOM15: only one callback from burst');
  }

  // --- DOM Test 16: startFrameClock / stopFrameClock with interval fallback ---
  console.log('--- DOM Test 16: frameClock interval fallback ---');
  {
    const { ui, clock, elements } = createDomSetup();
    const frames = [];
    elements.videoEl.readyState = 3; // HAVE_FUTURE_DATA
    elements.videoEl.currentTime = 0;

    ui.startFrameClock(() => frames.push(1));

    // Simulate time passing with changing currentTime
    elements.videoEl.currentTime = 0.033;
    clock.advance(250);
    assert(frames.length === 1, 'DOM16: frame tick fired');

    elements.videoEl.currentTime = 0.066;
    clock.advance(250);
    assert(frames.length === 2, 'DOM16: second tick');

    // Idempotent: second call should not install another
    ui.startFrameClock(() => frames.push(99));
    clock.advance(250);
    // Still uses original callback
    elements.videoEl.currentTime = 0.1;
    clock.advance(250);
    assert(frames.length >= 3, 'DOM16: idempotent — still tracking');

    ui.stopFrameClock();
    elements.videoEl.currentTime = 0.2;
    clock.advance(500);
    // No more ticks
    const count = frames.length;
    assert(frames.length === count, 'DOM16: stopped — no more ticks');
  }

  // --- DOM Test 17: stats toggle ---
  console.log('--- DOM Test 17: stats toggle ---');
  {
    const { ui, elements, eventListeners } = createDomSetup();
    let toggled = false;
    ui.bindIntents({ onToggleStats: (v) => { toggled = v; } });

    const statsClicks = eventListeners.statsBtn.click;
    assert(statsClicks.length >= 1, 'DOM17: stats click handler');

    // Click once → show
    statsClicks[0]();
    assert(elements.statsBox.style.display === 'block', 'DOM17: stats shown');
    assert(elements.statsBtn.textContent === 'Hide stats', 'DOM17: btn text Hide stats');
    assert(toggled === true, 'DOM17: onToggleStats(true)');

    // Click again → hide
    statsClicks[0]();
    assert(elements.statsBox.style.display === 'none', 'DOM17: stats hidden');
    assert(elements.statsBtn.textContent === 'Show stats', 'DOM17: btn text Show stats');
    assert(toggled === false, 'DOM17: onToggleStats(false)');
  }

  // ═══════════════════════════════════════════════════════════
  // Summary
  // ═══════════════════════════════════════════════════════════
  console.log(`\nAdapter tests: ${passed} passed, ${failed} failed`);
  if (failed > 0) process.exit(1);
  console.log('OK: adapter tests passed');
}

main().catch((e) => { console.error(e); process.exit(1); });
