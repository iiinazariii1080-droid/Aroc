/* Unit tests for config.js utility functions + bootstrap.js pure helpers (Node, no browser). */
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

function main() {
  const root = path.resolve(__dirname, '..');

  const sandbox = vm.createContext({
    window: {},
    console,
    Math: Math,
    document: { body: null, readyState: 'complete', addEventListener: () => {} },
    setTimeout: (fn, ms) => 0,
    clearTimeout: () => {},
    fetch: async () => ({ ok: false }),
    Janus: {
      randomString: () => 'deadbeef',
      init: (o) => o.callback && o.callback(),
      isWebrtcSupported: () => true,
    },
  });

  loadScript(sandbox, path.join(root, 'player', 'ns.js'));
  loadScript(sandbox, path.join(root, 'player', 'core', 'player_state.js'));
  loadScript(sandbox, path.join(root, 'player', 'core', 'codes.js'));

  // Load config.js — needs document.body for computeConfig but utility functions work standalone.
  loadScript(sandbox, path.join(root, 'player', 'config.js'));

  const Config = sandbox.window.AutonomousPlayer.Config;

  // ═══════════════════════════════════════════════
  // clampInt()
  // ═══════════════════════════════════════════════
  console.log('--- clampInt ---');

  assert(Config.clampInt(50, 10, 100, 0) === 50, 'clampInt: normal value within range');
  assert(Config.clampInt(5, 10, 100, 0) === 10, 'clampInt: below min clamps to min');
  assert(Config.clampInt(200, 10, 100, 0) === 100, 'clampInt: above max clamps to max');
  assert(Config.clampInt(10, 10, 100, 0) === 10, 'clampInt: exactly min returns min');
  assert(Config.clampInt(100, 10, 100, 0) === 100, 'clampInt: exactly max returns max');
  assert(Config.clampInt('abc', 10, 100, 42) === 42, 'clampInt: NaN string returns fallback');
  assert(Config.clampInt(null, 10, 100, 42) === 10, 'clampInt: null → Number(null)=0 → clamped to min');
  assert(Config.clampInt(undefined, 10, 100, 42) === 42, 'clampInt: undefined → NaN → fallback');
  assert(Config.clampInt(10.9, 1, 100, 0) === 10, 'clampInt: float truncated (10.9 → 10)');
  assert(Config.clampInt(10.1, 1, 100, 0) === 10, 'clampInt: float truncated (10.1 → 10)');
  assert(Config.clampInt(-5, -10, 100, 0) === -5, 'clampInt: negative within range');
  assert(Config.clampInt(Infinity, 10, 100, 42) === 42, 'clampInt: Infinity returns fallback');
  assert(Config.clampInt(-Infinity, 10, 100, 42) === 42, 'clampInt: -Infinity returns fallback');
  assert(Config.clampInt(NaN, 10, 100, 42) === 42, 'clampInt: NaN returns fallback');

  // ═══════════════════════════════════════════════
  // resolveFeatureFlag()
  // ═══════════════════════════════════════════════
  console.log('--- resolveFeatureFlag ---');

  // Truthy strings
  assert(Config.resolveFeatureFlag('1', false) === true, 'resolveFeatureFlag: "1" → true');
  assert(Config.resolveFeatureFlag('always', false) === true, 'resolveFeatureFlag: "always" → true');
  assert(Config.resolveFeatureFlag('on', false) === true, 'resolveFeatureFlag: "on" → true');
  assert(Config.resolveFeatureFlag('true', false) === true, 'resolveFeatureFlag: "true" → true');
  assert(Config.resolveFeatureFlag('yes', false) === true, 'resolveFeatureFlag: "yes" → true');

  // Case insensitive
  assert(Config.resolveFeatureFlag('TRUE', false) === true, 'resolveFeatureFlag: "TRUE" → true');
  assert(Config.resolveFeatureFlag('Yes', false) === true, 'resolveFeatureFlag: "Yes" → true');
  assert(Config.resolveFeatureFlag('ON', false) === true, 'resolveFeatureFlag: "ON" → true');

  // Falsy strings
  assert(Config.resolveFeatureFlag('0', true) === false, 'resolveFeatureFlag: "0" → false');
  assert(Config.resolveFeatureFlag('never', true) === false, 'resolveFeatureFlag: "never" → false');
  assert(Config.resolveFeatureFlag('off', true) === false, 'resolveFeatureFlag: "off" → false');
  assert(Config.resolveFeatureFlag('false', true) === false, 'resolveFeatureFlag: "false" → false');
  assert(Config.resolveFeatureFlag('no', true) === false, 'resolveFeatureFlag: "no" → false');

  // Unknown strings use fallback
  assert(Config.resolveFeatureFlag('maybe', true) === true, 'resolveFeatureFlag: unknown + fallback true → true');
  assert(Config.resolveFeatureFlag('maybe', false) === false, 'resolveFeatureFlag: unknown + fallback false → false');
  assert(Config.resolveFeatureFlag('', true) === true, 'resolveFeatureFlag: empty + fallback true → true');
  assert(Config.resolveFeatureFlag('', false) === false, 'resolveFeatureFlag: empty + fallback false → false');

  // Null/undefined
  assert(Config.resolveFeatureFlag(null, true) === true, 'resolveFeatureFlag: null + fallback true → true');
  assert(Config.resolveFeatureFlag(undefined, false) === false, 'resolveFeatureFlag: undefined + fallback false → false');

  // ═══════════════════════════════════════════════
  // parseQueryBool()
  // ═══════════════════════════════════════════════
  console.log('--- parseQueryBool ---');

  // Simulate URLSearchParams
  const params1 = new (function(){
    const data = new Map([['autoplay', '1'], ['debug', '0'], ['empty', '']]);
    this.has = (k) => data.has(k);
    this.get = (k) => data.has(k) ? data.get(k) : null;
  })();

  assert(Config.parseQueryBool(params1, 'autoplay') === true, 'parseQueryBool: "1" → true');
  assert(Config.parseQueryBool(params1, 'debug') === false, 'parseQueryBool: "0" → false');
  assert(Config.parseQueryBool(params1, 'missing') === null, 'parseQueryBool: absent key → null');
  assert(Config.parseQueryBool(params1, 'empty') === false, 'parseQueryBool: empty string → false (resolveFeatureFlag with empty → false)');

  // Null value
  const params2 = new (function(){
    this.has = (k) => k === 'flag';
    this.get = (k) => null;
  })();
  assert(Config.parseQueryBool(params2, 'flag') === null, 'parseQueryBool: key present with null value → null');

  // ═══════════════════════════════════════════════
  // joinUrl()
  // ═══════════════════════════════════════════════
  console.log('--- joinUrl ---');

  assert(Config.joinUrl('http://host', 'path') === 'http://host/path', 'joinUrl: basic');
  assert(Config.joinUrl('http://host/', '/path') === 'http://host/path', 'joinUrl: strips trailing/leading slashes');
  assert(Config.joinUrl('http://host///', '///path') === 'http://host/path', 'joinUrl: multiple slashes stripped');
  assert(Config.joinUrl('http://host', '') === 'http://host', 'joinUrl: empty suffix');
  assert(Config.joinUrl('http://host', null) === 'http://host', 'joinUrl: null suffix returns base');
  assert(Config.joinUrl('http://host', undefined) === 'http://host', 'joinUrl: undefined suffix returns base');
  assert(Config.joinUrl('http://host/api', 'v1') === 'http://host/api/v1', 'joinUrl: base with path + suffix');
  assert(Config.joinUrl('ws://h:8088', 'janus') === 'ws://h:8088/janus', 'joinUrl: websocket scheme');

  // ═══════════════════════════════════════════════
  // bootstrap.js pure helpers
  // ═══════════════════════════════════════════════
  console.log('--- bootstrap helpers ---');

  // Load remaining dependencies for bootstrap
  loadScript(sandbox, path.join(root, 'player', 'core', 'domain_events.js'));
  loadScript(sandbox, path.join(root, 'player', 'core', 'connection_policy.js'));
  loadScript(sandbox, path.join(root, 'player', 'core', 'state_machine_canonical.js'));
  loadScript(sandbox, path.join(root, 'player', 'core', 'invariants.js'));
  loadScript(sandbox, path.join(root, 'player', 'core', 'fail_closed.js'));
  loadScript(sandbox, path.join(root, 'player', 'core', 'backoff.js'));
  loadScript(sandbox, path.join(root, 'player', 'core', 'recovery_policy.js'));
  loadScript(sandbox, path.join(root, 'player', 'adapters', 'clock.js'));
  loadScript(sandbox, path.join(root, 'player', 'adapters', 'logger.js'));

  // bootstrap.js exposes sanitizeRtcConfig, createEventRingBuffer
  // We need to load it but avoid the auto-boot at the bottom
  // Re-create sandbox with document.readyState = 'complete' so boot() is called immediately
  // but it will fail because document.body is null → computeConfig returns null → early return
  // This is fine — we just need the helper functions available.

  // Actually bootstrap tries to call boot() synchronously via document.readyState check.
  // Let's override to prevent that.
  sandbox.document = {
    body: null,
    readyState: 'loading',
    addEventListener: () => {},
  };
  sandbox.alert = () => {};

  loadScript(sandbox, path.join(root, 'player', 'bootstrap.js'));

  // createEventRingBuffer is not exported... it's local to the IIFE. But sanitizeRtcConfig is also local.
  // Let's test what we can by reaching bootstrap through its effects.

  // --- createConsoleLogger ---
  console.log('--- createConsoleLogger ---');

  const AP = sandbox.window.AutonomousPlayer;
  const logFn = AP.Adapters.createConsoleLogger;

  const logger = logFn({ debug: true, prefix: '[Test]', run_id: 'r123' });
  assert(typeof logger.debug === 'function', 'logger has debug');
  assert(typeof logger.info === 'function', 'logger has info');
  assert(typeof logger.warn === 'function', 'logger has warn');
  assert(typeof logger.error === 'function', 'logger has error');

  // Non-debug logger should still have debug function (it's a no-op)
  const quietLog = logFn({ debug: false });
  assert(typeof quietLog.debug === 'function', 'quiet logger has debug');

  // --- createClock ---
  console.log('--- createClock ---');

  const createClock = AP.Adapters.createClock;
  // createClock uses window.setTimeout etc. - need to provide them
  sandbox.window.setTimeout = (fn, ms) => {
    const id = Math.random();
    if (typeof fn === 'function') setTimeout(fn, 0); // fire immediately for test
    return id;
  };
  sandbox.window.clearTimeout = () => {};
  sandbox.window.setInterval = (fn, ms) => Math.random();
  sandbox.window.clearInterval = () => {};
  sandbox.Date = Date;

  const clock = createClock();
  assert(typeof clock.nowMs === 'function', 'clock has nowMs');
  assert(typeof clock.setTimeout === 'function', 'clock has setTimeout');
  assert(typeof clock.clearTimeout === 'function', 'clock has clearTimeout');
  assert(typeof clock.setInterval === 'function', 'clock has setInterval');
  assert(typeof clock.clearInterval === 'function', 'clock has clearInterval');
  assert(typeof clock.debugSnapshot === 'function', 'clock has debugSnapshot');

  const snap = clock.debugSnapshot();
  assert(snap.timeouts === 0, 'initial snapshot: 0 timeouts');
  assert(snap.intervals === 0, 'initial snapshot: 0 intervals');

  // setTimeout increments counter, clearTimeout decrements
  const tid = clock.setTimeout(() => {}, 10000);
  const snap2 = clock.debugSnapshot();
  assert(snap2.timeouts === 1, 'after setTimeout: 1 timeout tracked');
  clock.clearTimeout(tid);
  const snap3 = clock.debugSnapshot();
  assert(snap3.timeouts === 0, 'after clearTimeout: 0 timeouts');

  // setInterval increments counter, clearInterval decrements
  const iid = clock.setInterval(() => {}, 1000);
  const snap4 = clock.debugSnapshot();
  assert(snap4.intervals === 1, 'after setInterval: 1 interval tracked');
  clock.clearInterval(iid);
  const snap5 = clock.debugSnapshot();
  assert(snap5.intervals === 0, 'after clearInterval: 0 intervals');

  // nowMs returns a number
  const now = clock.nowMs();
  assert(typeof now === 'number' && Number.isFinite(now), 'nowMs returns finite number');

  // ═══════════════════════════════════════════════
  // Summary
  // ═══════════════════════════════════════════════
  console.log(`\nConfig tests: ${passed} passed, ${failed} failed`);
  if (failed > 0) process.exit(1);
  console.log('OK: config tests passed');
}

main();
