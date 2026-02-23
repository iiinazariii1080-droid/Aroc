(function(){
  'use strict';
  const AP = window.AutonomousPlayer;
  if (!AP) throw new Error('AutonomousPlayer namespace missing');

  const DEFAULT_ICE = [{ urls: ['stun:stun.l.google.com:19302'] }];

  function ensureJanusInit(cfg){
    if (typeof Janus === 'undefined') {
      return Promise.reject(new Error(
        'Janus is not defined. The janus.js script did not load (e.g. 404 from /api/v1/.../janus.js). ' +
        'Add the Janus gateway JavaScript library to templates/janus.js on the server.'
      ));
    }
    if (window.__autonomousJanusInitDone) return Promise.resolve(true);
    return new Promise((resolve) => {
      Janus.init({
        debug: cfg.debug ? 'all' : ['warn', 'error'],
        callback: () => {
          window.__autonomousJanusInitDone = true;
          resolve(true);
        },
      });
    });
  }

  function sanitizeRtcConfig(iceServers, iceTransportPolicy, log){
    let hasTurn = false;
    let hasTurns = false;
    let hasCredentials = false;
    const types = { stun: 0, turn: 0, turns: 0 };
    for (const s of iceServers) {
      const urls = s && (Array.isArray(s.urls) ? s.urls : (s.url ? [s.url] : []));
      for (const u of urls) {
        const lower = String(u || '').toLowerCase();
        if (lower.startsWith('turn:')) { types.turn++; hasTurn = true; }
        else if (lower.startsWith('turns:')) { types.turns++; hasTurns = true; }
        else if (lower.startsWith('stun:')) types.stun++;
      }
      if (s && (s.username != null || s.credential != null)) hasCredentials = true;
    }
    if (iceTransportPolicy === 'relay' && !hasTurn && !hasTurns) {
      log.warn('rtc_config_relay_no_turn', { iceTransportPolicy, types });
    }
    if (hasCredentials) {
      log.info('rtc_config_credentials', { hasCredentials: true });
    }
    const frozenServers = iceServers.map((s) => Object.freeze(Object.assign({}, s)));
    const rtcConfig = Object.freeze({
      iceServers: Object.freeze(frozenServers),
      iceTransportPolicy,
    });
    log.info('rtc_config_loaded', {
      iceTransportPolicy,
      iceServers_count: frozenServers.length,
      server_types: types,
    });
    return rtcConfig;
  }

  async function loadRtcConfig(cfg, log){
    const url = `${window.location.origin}${cfg.clientConfigPath}`;
    try {
      const resp = await fetch(url, { cache: 'no-store' });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      const iceServers = Array.isArray(data.iceServers) && data.iceServers.length ? data.iceServers : DEFAULT_ICE;
      const iceTransportPolicy = (data.iceTransportPolicy === 'relay' || data.iceTransportPolicy === 'all')
        ? data.iceTransportPolicy
        : 'all';
      return sanitizeRtcConfig(iceServers, iceTransportPolicy, log);
    } catch (e) {
      log.warn('rtc_config_fallback', { error: String(e?.message || e) });
      return sanitizeRtcConfig(DEFAULT_ICE, 'all', log);
    }
  }

  const RING_BUFFER_SIZE = 50;
  function createEventRingBuffer(size){
    const buf = [];
    return {
      push: (entry) => {
        buf.push(entry);
        if (buf.length > size) buf.shift();
      },
      get: () => buf.slice(),
      clear: () => buf.length = 0,
    };
  }

  async function boot(){
    const cfg = AP.Config.computeConfig();
    if (!cfg) return;

    cfg.run_id = Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
    const clock = AP.Adapters.createClock();
    const log = AP.Adapters.createConsoleLogger({ debug: cfg.debug, prefix: '[AutonomousPlayer]', run_id: cfg.run_id });

    const eventRingBuffer = createEventRingBuffer(RING_BUFFER_SIZE);
    window.__playerEventRingBuffer = eventRingBuffer;

    await ensureJanusInit(cfg);
    if (!Janus.isWebrtcSupported()) {
      alert('WebRTC not supported');
      return;
    }

    if (cfg.textOnly) {
      const rtcConfig = await loadRtcConfig(cfg, log);
      const session = new AP.Adapters.JanusSessionManager(cfg, log);
      session.setRtcConfig(rtcConfig);
      const textroom = new AP.Adapters.JanusTextRoomAdapter(cfg, log, session);
      const joystick = new AP.App.JoystickService(cfg, log, textroom);
      await session.init(rtcConfig);
      if (joystick && typeof joystick.boot === 'function') await joystick.boot();
      return;
    }

    const rtcConfig = await loadRtcConfig(cfg, log);

    const session = new AP.Adapters.JanusSessionManager(cfg, log);
    session.setRtcConfig(rtcConfig);

    const ui = new AP.Adapters.DomUIAdapter(cfg, log, clock);
    const streaming = new AP.Adapters.JanusStreamingAdapter(cfg, log, session, clock);
    const textroom = cfg.textroomEnabled ? new AP.Adapters.JanusTextRoomAdapter(cfg, log, session) : null;
    const joystick = textroom ? new AP.App.JoystickService(cfg, log, textroom) : null;
    const stats = new AP.App.StatsService(cfg, clock, log, streaming);
    if (joystick) stats.setJoystickService(joystick);

    const controller = new AP.App.PlayerController(cfg, rtcConfig, ui, clock, log, streaming, stats, joystick);
    controller._eventRingBuffer = eventRingBuffer;
    window.autonomousPlayerController = controller;

    try {
      await controller.init();
    } catch (e) {
      const msg = e && e.message ? String(e.message) : String(e);
      log.error('boot_failed', { error: msg });
      ui.render({
        state: AP.Core.PlayerState.ERROR,
        desiredPlaying: false,
        errCode: AP.Core.PlayerErrorCode ? AP.Core.PlayerErrorCode.BOOT_FAILED : 'boot_failed',
        attempt: 0,
        debugText: msg,
      });
    }
  }

  // Support both classic <script> usage (DOMContentLoaded) and late dynamic injection.
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
