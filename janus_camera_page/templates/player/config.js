(function(){
  'use strict';
  const AP = window.AutonomousPlayer;
  if (!AP) throw new Error('AutonomousPlayer namespace missing');

  function resolveFeatureFlag(raw, fallback){
    const normalized = String(raw ?? '').trim().toLowerCase();
    if (['1','always','on','true','yes'].includes(normalized)) return true;
    if (['0','never','off','false','no'].includes(normalized)) return false;
    return !!fallback;
  }

  function parseQueryBool(params, key){
    if (!params.has(key)) return null;
    const v = params.get(key);
    if (v === null) return null;
    return resolveFeatureFlag(v, false);
  }

  function clampInt(raw, min, max, fallback){
    const n = Number(raw);
    if (!Number.isFinite(n)) return fallback;
    const i = Math.trunc(n);
    return Math.min(max, Math.max(min, i));
  }

  function joinUrl(base, suffix){
    if (!suffix) return base;
    const trimmedBase = String(base).replace(/\/+$/, '');
    const trimmedSuffix = String(suffix).replace(/^\/+/, '');
    if (!trimmedSuffix) return trimmedBase;
    return `${trimmedBase}/${trimmedSuffix}`;
  }

  function computeConfig(){
    const body = document.body;
    if (!body) return null;
    const dataset = body.dataset || {};
    const params = new URLSearchParams(window.location.search);

    const autoplayUrl = parseQueryBool(params, 'autoplay');
    const autoplayEnabled = autoplayUrl !== null ? autoplayUrl : resolveFeatureFlag(dataset.autoplay, false);

    const debugEnabled = resolveFeatureFlag(params.get('debug'), resolveFeatureFlag(dataset.debug, false));
    const debugPanelUrl = parseQueryBool(params, 'debug_panel');
    const debugPanelEnabled = debugPanelUrl !== null ? debugPanelUrl : resolveFeatureFlag(dataset.debugPanel, false);

    const HOST = window.location.host;
    const HOSTNAME = window.location.hostname;
    const PORT = window.location.port;
    const PROTOCOL = window.location.protocol;
    const WS = PROTOCOL === 'https:' ? 'wss' : 'ws';

    const CAM_TYPE = dataset.camType || '__CAM_TYPE__';
    const apiPrefixMode = String(dataset.apiPrefixMode || 'port').toLowerCase();
    const apiPrefix = dataset.apiPrefix || `/api/v1/${CAM_TYPE}`;

    function applyTemplate(value){
      if (!value) return value;
      return String(value).replace(/\{(hostname|host|protocol|wsprotocol|origin)\}/gi, (_, token) => {
        switch(token.toLowerCase()){
          case 'hostname': return HOSTNAME;
          case 'host': return HOST;
          case 'protocol': return PROTOCOL.replace(/:$/, '');
          case 'wsprotocol': return WS;
          case 'origin': return window.location.origin;
          default: return _;
        }
      });
    }

    const restBaseOverride = dataset.restBase ? applyTemplate(dataset.restBase) : null;
    const wsBaseOverride = dataset.wsBase ? applyTemplate(dataset.wsBase) : null;

    let restBase = restBaseOverride || `${PROTOCOL}//${HOST}`;
    let wsBase = wsBaseOverride || `${WS}://${HOST}`;

    const shouldApplyPrefix = apiPrefixMode === 'always' || (apiPrefixMode === 'port' && PORT !== '8900');
    if (!restBaseOverride && shouldApplyPrefix) restBase = joinUrl(restBase, apiPrefix);
    if (!wsBaseOverride && shouldApplyPrefix) wsBase = joinUrl(wsBase, apiPrefix);

    const janusWs = joinUrl(wsBase, 'janus-ws');
    const janusRest = joinUrl(restBase, 'janus');
    const robotRest = `${PROTOCOL}//${HOST}/api/v1/robot`;

    const isLocal =
      HOSTNAME === 'localhost' ||
      HOSTNAME === '127.0.0.1' ||
      /^192\.168\./.test(HOSTNAME) ||
      /^10\./.test(HOSTNAME) ||
      /^172\.(1[6-9]|2\d|3[0-1])\./.test(HOSTNAME);

    const autoRestartUrl = parseQueryBool(params, 'auto_restart');
    const autoRestartEnabledRaw = autoRestartUrl !== null ? autoRestartUrl : resolveFeatureFlag(dataset.autoRestart ?? 'local', isLocal);

    // Non-negotiable: autoplay implies autonomous recovery.
    const autoRestartEnabled = autoplayEnabled ? true : autoRestartEnabledRaw;
    const autonomousEnabled = autoplayEnabled || autoRestartEnabled;

    const joystickUrl = parseQueryBool(params, 'joystick');
    const joystickEnabled = joystickUrl !== null ? joystickUrl : resolveFeatureFlag(dataset.joystickMode ?? 'auto', isLocal);
    const streamOnlyUrl = parseQueryBool(params, 'streamOnly');
    const textOnlyUrl = parseQueryBool(params, 'textOnly');
    const streamOnly = streamOnlyUrl === true;
    const textOnly = textOnlyUrl === true;
    const textroomEnabled = textOnly ? true : (streamOnly ? false : resolveFeatureFlag(dataset.textroom ?? (joystickEnabled ? 'on' : 'off'), joystickEnabled));

    const noFrameThresholdMs = clampInt(dataset.noFrameThresholdMs, 2000, 60000, 5000);
    const watchdogTickMs = clampInt(dataset.watchdogIntervalMs, 500, 10000, 2000);
    const trackMuteRestartMs = clampInt(dataset.trackMuteMs, 500, 15000, 3000);

    const backoffBaseMs = clampInt(dataset.backoffBaseMs, 200, 5000, 500);
    const backoffMinMs = clampInt(dataset.backoffMinMs, 100, 2000, 250);
    const backoffMaxMs = clampInt(dataset.backoffMaxMs, 2000, 60000, 15000);
    const backoffFactor = (() => {
      const raw = Number(dataset.backoffFactor);
      return Number.isFinite(raw) ? Math.min(4.0, Math.max(1.2, raw)) : 1.8;
    })();
    const backoffJitterRatio = (() => {
      const raw = Number(dataset.backoffJitterRatio);
      return Number.isFinite(raw) ? Math.min(0.8, Math.max(0.0, raw)) : 0;
    })();
    // Reconnect attempt limit: default from AP.Core.MAX_RECONNECT_ATTEMPTS, clamped [3, 50]. Exhausted -> single transition to ERROR (L15: timers cleared).
    const maxReconnectAttempts = clampInt(dataset.maxReconnectAttempts, 3, 50, AP.Core.MAX_RECONNECT_ATTEMPTS ?? 12);
    const iceDisconnectedGraceMs = clampInt(dataset.iceDisconnectedGraceMs, 1000, 30000, 5000);
    const connectSettleMs = clampInt(dataset.connectSettleMs, 500, 20000, 6000);
    const settleStartTimeoutMs = clampInt(dataset.settleStartTimeoutMs, 3000, 60000, 15000);
    const reconnectAttemptTimeoutMs = clampInt(dataset.reconnectAttemptTimeoutMs, 5000, 60000, 15000);
    const maxWatchRetries = clampInt(dataset.maxWatchRetries, 0, 20, 3);
    const maxReattachRetries = clampInt(dataset.maxReattachRetries, 0, 20, 2);

    // ERROR state auto-recovery: retry after exponential delay (base * 2^count, capped at max).
    const errorAutoRetryBaseMs = clampInt(dataset.errorAutoRetryBaseMs, 5000, 60000, 10000);
    const errorAutoRetryMaxMs = clampInt(dataset.errorAutoRetryMaxMs, 30000, 600000, 120000);

    // When true (default): reconnect only while tab is visible; on tab visible again, auto-retry if ERROR or resume RECONNECTING.
    const visibilityAwareReconnect = resolveFeatureFlag(dataset.visibilityAwareReconnect, true);
    const preferStreamId = dataset.preferStreamId ? parseInt(dataset.preferStreamId, 10) : null;
    const streamName = dataset.streamName || 'RealSense Stream';
    const clientConfigPath = dataset.clientConfigPath || `/api/v1/${CAM_TYPE}/client-config`;

    return {
      debug: !!debugEnabled,

      // feature flags
      autoplayEnabled,
      autoRestartEnabled,
      autonomousEnabled,
      debugPanelEnabled,
      joystickEnabled,
      joystickHttp: resolveFeatureFlag(dataset.joystickHttp ?? '', false),
      textroomEnabled,
      streamOnly,
      textOnly,

      // dom ids
      videoId: dataset.videoId || 'video',
      playButtonId: dataset.playButtonId || 'playBtn',
      statsButtonId: dataset.statsBtnId || dataset.statsButtonId || 'statsBtn',
      statsBoxId: dataset.statsBoxId || 'statsBox',
      statusPillId: dataset.statusPillId || 'statusPill',
      debugPanelId: dataset.debugPanelId || 'debugPanel',

      // stream selection
      preferStreamId,
      streamName,

      // endpoints
      restBase,
      robotRest,
      janusWs,
      janusRest,
      clientConfigPath,

      // watchdog
      noFrameThresholdMs,
      watchdogTickMs,
      trackMuteRestartMs,
      iceDisconnectedGraceMs,
      connectSettleMs,
      settleStartTimeoutMs,
      reconnectAttemptTimeoutMs,

      // stats
      statsIntervalMs: 1000,

      // reconnect backoff
      backoffBaseMs,
      backoffMinMs,
      backoffMaxMs,
      backoffFactor,
      backoffJitterRatio,
      maxReconnectAttempts,
      maxWatchRetries,
      maxReattachRetries,
      errorAutoRetryBaseMs,
      errorAutoRetryMaxMs,
      visibilityAwareReconnect,
    };
  }

  AP.Config = {
    computeConfig,
    resolveFeatureFlag,
    parseQueryBool,
    clampInt,
    joinUrl,
  };
})();
