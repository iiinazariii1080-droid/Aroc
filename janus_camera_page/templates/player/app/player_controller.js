(function(){
  'use strict';
  const AP = window.AutonomousPlayer;
  if (!AP) throw new Error('AutonomousPlayer namespace missing');

  const PlayerState = AP.Core.PlayerState;
  const RecoverySeverity = AP.Core.RecoverySeverity;
  const RecoveryReason = AP.Core.RecoveryReason;
  const PlayerErrorCode = AP.Core.PlayerErrorCode;
  const DomainEventType = AP.Core.DomainEventType;
  const PolicyAction = AP.Core.PolicyAction;
  const ConnectionPolicy = AP.Core.ConnectionPolicy;
  const EventType = AP.Core.EventType;
  const ActionType = AP.Core.ActionType;
  const StateMachineCanonical = AP.Core.StateMachineCanonical;
  const InvariantGate = AP.Core.InvariantGate;
  const InvariantViolation = AP.Core.InvariantViolation;

  const defaultSeverityForReason = AP.App.RecoveryPolicy.defaultSeverityForReason;

  /**
   * PlayerController: state machine and stream event handling.
   * - Events are the single source of truth; timers only emit "time's up" and policy decides.
   * - Definition of Connected: webrtcUp AND firstVideoFrameReceived (ConnectionPolicy.isConnected).
   * - One session epoch: _sessionToken invalidates stale async work; only one active connect/reconnect.
   *   Events from previous token/gen are ignored. Adapters use _handleGen to ignore stale callbacks.
   */
  class PlayerController {
    /**
     * @param {any} cfg
     * @param {any} rtcConfig
     * @param {AP.Ports.VideoPort} ui
     * @param {AP.Ports.ClockPort} clock
     * @param {AP.Ports.LoggerPort} logger
     * @param {AP.Ports.StreamingPort} streaming
     * @param {any} statsService
     * @param {any} joystickService
     */
    constructor(cfg, rtcConfig, ui, clock, logger, streaming, statsService, joystickService){
      this.cfg = cfg;
      this.rtcConfig = rtcConfig;
      this.ui = ui;
      this.clock = clock;
      this.log = logger;
      this.streaming = streaming;
      this.stats = statsService;
      this.joystick = joystickService;
      if (typeof this.streaming.watch !== 'function' || typeof this.streaming.stop !== 'function') {
        throw new Error('PlayerController: streaming port must implement watch and stop');
      }
      if (typeof this.clock.setTimeout !== 'function' || typeof this.clock.clearTimeout !== 'function') {
        throw new Error('PlayerController: clock port must implement setTimeout and clearTimeout');
      }

      // state machine (single source of truth). System is always in exactly one PlayerState (A1).
      // DoD: this.state = is only allowed here (initial) and in _applySnapshot (result of handleEvent).
      this.state = PlayerState.IDLE;
      this.desiredPlaying = false;   // user intent / UI; not part of state enum
      this.errCode = '';

      // runtime snapshot (canonical snapshot fields used by state machine + InvariantGate)
      this.streamId = null;
      this.iceState = 'new';
      this.webrtcUp = false;

      // Definition of Connected: webrtcUp AND firstVideoFrameReceived (see ConnectionPolicy.isConnected)
      this._firstFrameLatch = false;

      // operation token: invalidates stale async continuations (connect/reconnect). One active session epoch.
      this._sessionToken = 0;

      // degraded: policy output (MARK_DEGRADED action); not part of state enum. ICE/webrtc down but media flowing.
      this._degraded = false;

      // latch: at most one _runConnectFlow() in progress (FSM can emit START_JANUS multiple times).
      this._connectInFlight = false;

      // latch: fire STREAM_RECOVERED at most once per connected period; reset when entering RECONNECTING.
      this._recoveryNotified = false;

      // ERROR auto-recovery: retry from ERROR state after increasing delay (autonomy guarantee).
      this._errorRetryTimer = null;
      this._errorRetryCount = 0;

      // watchdog (frame age + timeout callback; controller applies policy in callback)
      this._watchdog = new AP.App.WatchdogService(cfg, clock, (ageMs) => this._onWatchdogTimeout(ageMs));

      // Timers (ICE grace, track mute)
      this._timers = new AP.App.TimerCoordinator(clock);

      // Reconnect coordinator (sole owner of reconnect scheduling/counters)
      this._reconnect = new AP.App.ReconnectCoordinator(cfg, clock, logger);
      this._reconnect.bindContext({
        getToken: () => this._sessionToken,
        shouldContinue: () => this._isTabVisible() && this.cfg.autonomousEnabled && this.desiredPlaying && this.state !== PlayerState.ERROR,
        isRecovered: () => this._isConnected(),
        executeAttempt: (ctx) => this._executeRecoveryAttempt(ctx),
        onScheduled: () => {
          this._render();
        },
        onSuccess: () => {
          this.handleEvent({ type: EventType.RECONNECT_SUCCESS, generation: this._sessionToken });
        },
        onExhausted: (pending) => {
          this._fail(PlayerErrorCode.RECONNECT_EXHAUSTED, String(pending?.reason || 'unknown'));
        },
      });

      this._boundOnStreamEvent = (ev) => this._onStreamEvent(ev);
      this._boundOnVisibilityChange = () => this._onVisibilityChange();

      this._installIntents();
    }

    /** Used by visibility-aware reconnect: only run/continue reconnects while tab is visible. */
    _isTabVisible(){
      if (typeof document === 'undefined') return true;
      return document.visibilityState === 'visible';
    }

    /** On tab visible again: auto-retry if ERROR (exhausted); resume scheduling if RECONNECTING was paused. */
    _onVisibilityChange(){
      if (!this._isTabVisible()) return;
      if (this.state === PlayerState.ERROR) {
        this.retry();
        return;
      }
      if (this.state === PlayerState.RECONNECTING) this._reconnect.resumeIfPending();
    }

    _installIntents(){
      this.ui.bindIntents({
        onTogglePlay: () => this.togglePlay(),
        onRetry: () => this.retry(),
        onToggleStats: (on) => this._onStatsToggle(on),
      });
    }

    async init(){
      // wire stream events
      this.streaming.setEventSink(this._boundOnStreamEvent, () => this._sessionToken);

      // frame clock (installed once; safe against reconnect loops)
      this.ui.startFrameClock(() => this._onFrameReceived());

      if (this.cfg.visibilityAwareReconnect && typeof document !== 'undefined') {
        document.addEventListener('visibilitychange', this._boundOnVisibilityChange);
      }

      // joystick
      if (this.joystick) {
        this.joystick.setActivePredicate(() => this.desiredPlaying);
        await this.joystick.boot();
      }

      // initial render
      this._render();

      if (this.cfg.autoplayEnabled) {
        this.desiredPlaying = true;
        this.handleEvent({ type: EventType.PLAY_REQUEST, generation: this._sessionToken });
      }
    }

    // ------------
    // Intents
    // ------------

    togglePlay(){
      if (this.desiredPlaying) {
        this.desiredPlaying = false;
        this.handleEvent({ type: EventType.STOP_REQUEST, generation: this._sessionToken });
        return;
      }
      this.desiredPlaying = true;
      this.handleEvent({ type: EventType.PLAY_REQUEST, generation: this._sessionToken });
    }

    retry(){
      this.errCode = '';
      this.desiredPlaying = true;
      this.handleEvent({ type: EventType.RESET, generation: this._sessionToken });
    }

    _onStatsToggle(on){
      if (on) {
        this.stats.start((txt) => this.ui.setStatsText(txt));
      } else {
        this.stats.stop();
      }
    }

    // ------------
    // State & flow
    // ------------

    /** Returns true if continuation is stale (token or desiredPlaying changed). When true, logs EVENT_DROPPED. */
    _dropIfStale(token, context){
      if (token === this._sessionToken && this.desiredPlaying) return false;
      this.log.debug('EVENT_DROPPED', { stale: true, context, token, current: this._sessionToken });
      return true;
    }

    /** Build snapshot for canonical state machine (event, snapshot) -> { next, actions }. */
    _buildSnapshot(){
      return {
        state: this.state,
        generation: this._sessionToken,
        reconnectAttempts: this.state === PlayerState.RECONNECTING ? Math.max(1, this._reconnect.attempt()) : this._reconnect.attempt(),
        webrtcUp: this.webrtcUp,
        firstFrameReceived: this._firstFrameLatch,
        iceState: this.iceState || 'new',
      };
    }

    /** Apply next snapshot to controller state (L1, L2). Only place besides constructor that assigns this.state (DoD: single dispatch). */
    _applySnapshot(next, errorCode){
      if (!next) return;
      const prev = this.state;
      this.state = next.state;
      this._sessionToken = next.generation != null ? next.generation : this._sessionToken;
      this.webrtcUp = !!next.webrtcUp;
      this._firstFrameLatch = !!next.firstFrameReceived;
      if (next.iceState !== undefined) this.iceState = next.iceState;
      if (next.state === PlayerState.ERROR) {
        this.desiredPlaying = false;
        if (errorCode != null) this.errCode = errorCode;
        this._scheduleErrorAutoRetry();
      }
      if (next.state === PlayerState.RECONNECTING) this._recoveryNotified = false;
      // When entering PLAYING successfully, reset error retry counter (clean slate).
      if (next.state === PlayerState.PLAYING && prev !== PlayerState.PLAYING) {
        this._errorRetryCount = 0;
      }
      // When leaving RECONNECTING for PLAYING, reset coordinator to prevent stale attempt state
      // (e.g. false exhaustion if STREAM_RECOVERED bypassed coordinator's notifyRecovered).
      if (prev === PlayerState.RECONNECTING && next.state === PlayerState.PLAYING) {
        this._reconnect.reset();
      }
      // Leaving ERROR → cancel auto-retry timer
      if (prev === PlayerState.ERROR && next.state !== PlayerState.ERROR) {
        this._clearErrorAutoRetry();
      }
      if (prev !== next.state) {
        this.log.info('state_change', { event: 'STATE_TRANSITION', from: prev, to: next.state, token: this._sessionToken });
      }
    }

    /**
     * Single entry for all external inputs (SAFETY_LAWS). All UI, adapter, timer and recovery paths must go through handleEvent.
     * Side-effects are only performed in ActionExecutor; no business logic or mutations outside transition/apply/execute.
     * @param {{ type: string, reason?: string, severity?: number, generation?: number }} event
     */
    handleEvent(event){
      if (!event || !event.type) return;
      if (event.generation != null && event.generation !== this._sessionToken) {
        this.log.debug('EVENT_DROPPED', { stale: true, context: 'handleEvent', generation: event.generation, current: this._sessionToken });
        return;
      }
      const rb = this._eventRingBuffer;
      if (rb) rb.push({ type: `handle:${event.type}`, timestamp: this.clock.nowMs(), generation: this._sessionToken, run_id: this.cfg.run_id });
      const snapshot = this._buildSnapshot();
      let result = StateMachineCanonical.transition(event, snapshot);
      try {
        InvariantGate.check(result.next);
      } catch (e) {
        if (e && e.name === 'InvariantViolation') {
          this.log.error('INVARIANT_VIOLATION', { id: e.id, message: e.message });
          this.errCode = PlayerErrorCode.INVARIANT_VIOLATION;
          result = {
            next: {
              state: PlayerState.ERROR,
              generation: snapshot.generation,
              reconnectAttempts: snapshot.reconnectAttempts,
              webrtcUp: false,
              firstFrameReceived: false,
            },
            actions: [
              { type: ActionType.LOG, message: e.message },
              { type: ActionType.CANCEL_ALL_TIMERS },
              { type: ActionType.STOP_STREAMING },
            ],
          };
        } else {
          throw e;
        }
      }
      const prevState = snapshot.state;
      const nextState = result.next.state;
      if (prevState === PlayerState.CONNECTING && nextState === PlayerState.PLAYING) {
        const hasStop = result.actions.some((a) => a.type === ActionType.STOP_STREAMING || a.type === ActionType.STOP_JANUS);
        if (hasStop) {
          this.log.error('INVARIANT_VIOLATION', { id: 'NO_STOP_ON_PLAYING', message: 'CONNECTING→PLAYING must not emit STOP_STREAMING or STOP_JANUS' });
          throw new Error('NO_STOP_ON_PLAYING: CONNECTING→PLAYING must not emit STOP_STREAMING or STOP_JANUS');
        }
      }
      this._applySnapshot(result.next, result.next.state === PlayerState.ERROR ? this.errCode : undefined);
      this._executeActions(result.actions);
    }

    async _ensureStreamSelected(){
      if (this.streamId != null) return;
      const list = await this.streaming.listStreams();
      this.streamId = this._selectStreamId(list);
      this.log.info('stream_selected', { streamId: this.streamId });
    }

    async _ensureSessionAndWatch(token){
      // Ensure Janus session+handle.
      await this.streaming.init(this.rtcConfig);
      if (this._dropIfStale(token, 'ensure_started')) return;

      // Attach textroom (optional) after session exists.
      if (this.joystick) await this.joystick.ensureTextRoomAttached();
      if (this._dropIfStale(token, 'ensure_started')) return;

      // Pick stream (once) via barricaded selection logic.
      await this._ensureStreamSelected();
      if (this._dropIfStale(token, 'ensure_started')) return;

      // Start watch
      await this.streaming.watch(this.streamId);
      if (this._dropIfStale(token, 'ensure_started')) return;

      // Bind inbound stream to video
      this.ui.bindStream(this.streaming.getInboundStream());
    }

    async _reestablishWatch(token){
      // Used by recovery ladder.
      await this._ensureStreamSelected();
      if (this._dropIfStale(token, 'reestablish_watch')) return;

      await this.streaming.watch(this.streamId);
      if (this._dropIfStale(token, 'reestablish_watch')) return;

      this.ui.bindStream(this.streaming.getInboundStream());
      await this.ui.ensurePlaying();
    }

    _selectStreamId(list){
      if (this.cfg.preferStreamId && Number.isFinite(this.cfg.preferStreamId)) return this.cfg.preferStreamId;
      const name = String(this.cfg.streamName || '').toLowerCase();
      if (Array.isArray(list)) {
        const byName = list.find((s) => String(s.description || s.name || '').toLowerCase().includes(name));
        if (byName && byName.id != null) return byName.id;
        if (list.length && list[0].id != null) return list[0].id;
      }
      throw new Error('No streams available');
    }

    _fail(code, detail){
      this.errCode = code;
      this.desiredPlaying = false;
      this.log.error('player_error', { code, detail, token: this._sessionToken });
      this.handleEvent({ type: EventType.FORCE_ERROR, reason: detail || code, generation: this._sessionToken });
      if (code === PlayerErrorCode.AUTOPLAY_BLOCKED) this._render({ autoplayBlocked: true });
    }

    /**
     * Single point of timer cleanup (I4: IDLE/ERROR ⇒ no timers). L14/L15: timers are owned by state;
     * leaving RECONNECTING (or CONNECTING/PLAYING) cancels all—reconnect timers via _reconnect.reset(),
     * plus watchdog, ICE grace, track mute. A timer cannot resurrect state after exit (E3).
     * Only cancels timers; stopping the stream is done by the STOP_STREAMING action (IDLE/ERROR transitions).
     */
    _stopAll(){
      this._stopWatchdog();
      this._reconnect.reset();
      this._clearIceGrace();
      this._clearTrackMuteTimers();
      this._clearErrorAutoRetry();
      this._degraded = false;
      // Release connect latch so the next PLAY_REQUEST can start a fresh _runConnectFlow.
      // The stale async flow (if any) will exit via _dropIfStale on its next await.
      this._connectInFlight = false;
    }

    /** Critical actions: throw => fail-closed to ERROR. Non-critical: log warning and continue. */
    static _criticalActionTypes = new Set([
      ActionType.START_JANUS,
      ActionType.STOP_JANUS,
      ActionType.STOP_STREAMING,
      ActionType.START_RECONNECT_TIMER,
      ActionType.CANCEL_ALL_TIMERS,
      ActionType.BIND_STREAM,
      ActionType.START_RECONNECT_SETTLE,
    ]);

    /**
     * ActionExecutor: execute actions from canonical state machine (L2, L3).
     * Single place for all side-effects triggered by transitions.
     */
    _executeActions(actions){
      if (!Array.isArray(actions)) return;
      const critical = this.constructor._criticalActionTypes;
      for (const a of actions) {
        try {
          switch (a.type) {
            case ActionType.START_JANUS:
              this._runConnectFlow();
              break;
            case ActionType.STOP_JANUS:
              this.streaming.stop().catch((e) => this.log.warn('stop_failed', { reason: 'STOP_JANUS', error: String(e && e.message || e), token: this._sessionToken }));
              break;
            case ActionType.STOP_STREAMING:
              this.streaming.stop().catch((e) => this.log.warn('stop_failed', { reason: a.reason || 'stop_streaming', error: String(e && e.message || e), token: this._sessionToken }));
              this.log.info('stopped', { reason: a.reason || 'stop_streaming', token: this._sessionToken });
              break;
            case ActionType.START_RECONNECT_TIMER:
              this._reconnect.request(a.reason || 'unknown', a.severity != null ? a.severity : 1);
              break;
            case ActionType.CANCEL_ALL_TIMERS:
              this._stopAll(a.reason || 'state_exit');
              break;
            case ActionType.LOG:
              this.log.error('STATE_MACHINE_LOG', { message: a.message });
              break;
            case ActionType.RENDER:
              this._render();
              break;
            case ActionType.ARM_ICE_GRACE:
              this._armIceGrace();
              break;
            case ActionType.CANCEL_ICE_GRACE:
              this._clearIceGrace();
              break;
            case ActionType.ARM_TRACK_MUTE_TIMER:
              if (a.trackId != null) this._armTrackMuteTimer(a.trackId);
              break;
            case ActionType.DISARM_TRACK_MUTE_TIMER:
              if (a.trackId != null) this._disarmTrackMuteTimer(a.trackId);
              break;
            case ActionType.MARK_DEGRADED:
              this._degraded = true;
              this.log.debug('connection_degraded', { token: this._sessionToken });
              break;
            case ActionType.BIND_STREAM:
              this.ui.bindStream(this.streaming.getInboundStream());
              break;
            case ActionType.START_RECONNECT_SETTLE:
              if (this.state === PlayerState.RECONNECTING && this._reconnect.inFlight()) {
                this._reconnect.startSettleWindow();
              }
              break;
            default:
              break;
          }
        } catch (e) {
          const errMsg = String(e && e.message || e);
          if (critical.has(a.type)) {
            this.log.error('action_executor_error', { action: a.type, error: errMsg });
            this._fail(PlayerErrorCode.ACTION_FAILED, `${a.type}: ${errMsg}`);
            return;
          }
          this.log.warn('action_executor_error', { action: a.type, error: errMsg });
        }
      }
    }

    /**
     * Async connect flow: init, watch, bind, ensurePlaying, startWatchdog.
     * Assumes state is already CONNECTING and _sessionToken is set (e.g. after apply next).
     * Used by START_JANUS action and by _goConnect.
     * Guarded by _connectInFlight so overlapping START_JANUS does not run twice.
     */
    async _runConnectFlow(){
      if (this._connectInFlight) {
        this.log.warn('connect_flow_already_running');
        return;
      }
      this._connectInFlight = true;
      const token = this._sessionToken;
      try {
        await this._ensureSessionAndWatch(token);
        if (this._dropIfStale(token, 'connect')) return;
        const playRes = await this.ui.ensurePlaying();
        if (this._dropIfStale(token, 'connect_play')) return;
        if (!playRes.ok && playRes.blocked) {
          this._fail(PlayerErrorCode.AUTOPLAY_BLOCKED, 'Browser blocked autoplay');
          return;
        }
        this._startWatchdog();
      } catch (e) {
        if (this._dropIfStale(token, 'connect')) return;
        this._fail(PlayerErrorCode.CONNECT_FAILED, String(e?.message || e));
      } finally {
        this._connectInFlight = false;
      }
    }

    /** Definition of Connected: webrtcUp AND firstVideoFrameReceived (ConnectionPolicy). */
    _isConnected(){
      return ConnectionPolicy.isConnected({ webrtcUp: this.webrtcUp, firstFrameReceived: this._firstFrameLatch });
    }

    /** True if current state allows transitioning to PLAYING on first frame (CONNECTING or RECONNECTING with webrtcUp). */
    _canTransitionOnFrame(){
      return this.state === PlayerState.CONNECTING ||
        (this.state === PlayerState.RECONNECTING && this.webrtcUp);
    }

    /** Called on each frame tick. First-frame and recovered state are driven only by state machine (FIRST_FRAME_RECEIVED, STREAM_RECOVERED). */
    _onFrameReceived(){
      this._watchdog.updateFrameTime();
      if (!this._firstFrameLatch && this.webrtcUp && this._canTransitionOnFrame()) {
        this.handleEvent({ type: EventType.FIRST_FRAME_RECEIVED, generation: this._sessionToken });
      }
      // Re-check state after FIRST_FRAME_RECEIVED dispatch — it may have changed (e.g. invariant violation → ERROR).
      if (!this.desiredPlaying || !this._isConnected()) return;
      if (!this._canTransitionOnFrame()) return;
      if (this._recoveryNotified) return;
      if (this.state === PlayerState.ERROR || this.state === PlayerState.IDLE) return;
      this._recoveryNotified = true;
      this._tryNotifyRecovered();
      this.errCode = '';
      this.handleEvent({ type: EventType.STREAM_RECOVERED, generation: this._sessionToken });
    }

    /** Snapshot for ConnectionPolicy.decide (event-driven; no timers inside policy). */
    _buildPolicySnapshot(extra){
      const now = this.clock.nowMs();
      const lastFrameAge = this._watchdog.getLastFrameAgeMs(now);
      return Object.assign({
        state: this.state,
        webrtcUp: this.webrtcUp,
        firstFrameReceived: this._firstFrameLatch,
        iceState: this.iceState,
        lastFrameAgeMs: lastFrameAge,
        inReconnectInFlight: this._reconnect.inFlight(),
        desiredPlaying: this.desiredPlaying,
      }, extra || {});
    }

    _applyPolicyDecision(decision){
      if (!decision || decision.action === PolicyAction.NO_OP) return;
      if (decision.action === PolicyAction.MARK_DEGRADED) {
        this.handleEvent({ type: EventType.POLICY_MARK_DEGRADED, generation: this._sessionToken });
        return;
      }
      if (decision.action === PolicyAction.REQUEST_RECOVERY && decision.reason) {
        const sev = decision.severity != null ? decision.severity : defaultSeverityForReason(decision.reason, RecoverySeverity.SOFT);
        if (this.state === PlayerState.RECONNECTING && this._reconnect.inFlight()) {
          this._reconnect.notifyAttemptFailed(decision.reason, sev);
        } else {
          this.requestRecovery(decision.reason, sev);
        }
      }
    }

    // ------------
    // Stream events
    // ------------

    _onStreamEvent(ev){
      const type = ev && ev.type;
      const p = ev && ev.payload;
      if (ev.token != null && ev.token !== this._sessionToken) {
        this.log.debug('EVENT_DROPPED', { stale: true, context: 'stream_event', token: ev.token, current: this._sessionToken });
        return;
      }
      const rb = this._eventRingBuffer;
      if (rb && type) {
        const light = (payload) => {
          if (!payload || typeof payload !== 'object') return {};
          const o = {};
          if ('state' in payload) o.state = payload.state;
          if ('up' in payload) o.up = payload.up;
          if ('reason' in payload) o.reason = payload.reason;
          if ('error_code' in payload) o.error_code = payload.error_code;
          if ('where' in payload) o.where = payload.where;
          return o;
        };
        rb.push({ type: `stream:${type}`, timestamp: this.clock.nowMs(), generation: this._sessionToken, run_id: this.cfg.run_id, payload: light(p) });
      }

      if (!this.desiredPlaying && this.state !== PlayerState.CONNECTING && this.state !== PlayerState.RECONNECTING) {
        this.log.debug('EVENT_DROPPED', { stale: true, context: 'stream_event' });
        return;
      }

      const gen = this._sessionToken;
      const canReport = this.state === PlayerState.CONNECTING || this.state === PlayerState.PLAYING || this.state === PlayerState.RECONNECTING;

      switch (type) {
        case 'ICE_STATE':
          this.log.debug('ice_state', { state: p.state });
          if (p.state === 'failed' && canReport) {
            // ICE failed is terminal — skip ICE_REPORT and go straight to ICE_FAILED.
            this.handleEvent({ type: EventType.ICE_FAILED, reason: RecoveryReason.ICE_FAILED, severity: RecoverySeverity.HARD, generation: gen });
          } else if (canReport) {
            this.handleEvent({ type: EventType.ICE_REPORT, iceState: p.state, generation: gen });
          }
          break;
        case 'WEBRTC_STATE':
          this.log.debug('webrtc_state', { up: !!p.up, reason: p.reason });
          // In PLAYING, webrtcUp: false would violate L4 if applied as report; policy dispatches RECONNECT_SCHEDULED which sets webrtcUp in transition.
          if (canReport && (p.up || this.state !== PlayerState.PLAYING)) {
            this.handleEvent({ type: EventType.WEBRTC_REPORT, webrtcUp: !!p.up, reason: p.reason, generation: gen });
          }
          if (!p.up) {
            const reason = String(p.reason || '');
            const snapshot = this._buildPolicySnapshot({ webrtcDownReason: reason });
            const decision = ConnectionPolicy.decide(DomainEventType.WEBRTC_DOWN, snapshot);
            this._applyPolicyDecision(decision);
          } else {
            this._tryNotifyRecovered();
          }
          break;
        case 'HANGUP': {
          const hangupReason = String(p.reason || '');
          const hangupSnapshot = this._buildPolicySnapshot({ hangupReason });
          const hangupDecision = ConnectionPolicy.decide(DomainEventType.HANGUP, hangupSnapshot);
          this._applyPolicyDecision(hangupDecision);
          break;
        }
        case 'TRACK':
          if (p.on && canReport) this.handleEvent({ type: EventType.TRACK_READY, generation: gen });
          break;
        case 'TRACK_MUTED':
          if (canReport) this.handleEvent({ type: EventType.TRACK_MUTED, trackId: p.trackId, generation: gen });
          break;
        case 'TRACK_UNMUTED':
          if (canReport) this.handleEvent({ type: EventType.TRACK_UNMUTED, trackId: p.trackId, generation: gen });
          break;
        case 'TRACK_ENDED': {
          const endedSnapshot = this._buildPolicySnapshot();
          const endedDecision = ConnectionPolicy.decide(DomainEventType.TRACK_ENDED, endedSnapshot);
          this._applyPolicyDecision(endedDecision);
          break;
        }
        case 'SESSION_RESET':
          if (this.state === PlayerState.RECONNECTING && this._reconnect.inFlight()) {
            if (this._reconnect.consumeExpectedSessionReset()) break;
          }
          if (canReport) this.handleEvent({ type: EventType.WEBRTC_REPORT, webrtcUp: false, generation: gen });
          this._applyPolicyDecision(ConnectionPolicy.decide(DomainEventType.SESSION_RESET, this._buildPolicySnapshot()));
          break;
        case 'ERROR': {
          const errCode = p && (p.error_code ?? p.errorCode);
          const errMsg = p && (typeof p.error === 'string' ? p.error : (p.error && p.error.error) || (p.error && p.error.message) || '');
          const is460 = errCode === 460 || (typeof errMsg === 'string' && errMsg.includes('Already watching'));
          if (is460) {
            if (this.state === PlayerState.RECONNECTING && this._reconnect.inFlight()) {
              this._reconnect.notifyAttemptFailed(RecoveryReason.ALREADY_WATCHING, RecoverySeverity.HARD);
            } else {
              this.requestRecovery(RecoveryReason.ALREADY_WATCHING, RecoverySeverity.HARD);
            }
          } else {
            this._applyPolicyDecision(ConnectionPolicy.decide(DomainEventType.JANUS_ERROR, this._buildPolicySnapshot()));
          }
          break;
        }
        case 'STREAMING_OFFER_RECEIVED':
          if (canReport) this.handleEvent({ type: EventType.STREAMING_OFFER_RECEIVED, generation: gen });
          break;
        default:
          break;
      }
    }

    // ------------
    // Watchdog
    // ------------

    _onWatchdogTimeout(ageMs){
      if (!this.desiredPlaying) return;
      const iceNegotiating = this.iceState === 'new' || this.iceState === 'checking';
      if ((this.state === PlayerState.CONNECTING || this.state === PlayerState.PLAYING) && !iceNegotiating) {
        const snapshot = this._buildPolicySnapshot();
        const decision = ConnectionPolicy.decide(DomainEventType.MEDIA_SILENCE_TIMEOUT, snapshot);
        this._applyPolicyDecision(decision);
      }
    }

    _startWatchdog(){
      this._watchdog.start();
    }

    _stopWatchdog(){
      this._watchdog.stop();
    }

    _isDataPlaneHealthy(){
      const age = this._watchdog.getLastFrameAgeMs(this.clock.nowMs());
      return age < Math.max(1000, (this.cfg.watchdogTickMs || 2000) * 2);
    }

    /**
     * If we are in RECONNECTING with an attempt in flight and recovery condition is met
     * (webrtc up + data plane healthy), notify the coordinator so it completes successfully.
     * Transition to PLAYING and success are event-driven; coordinator settles only on notifyRecovered().
     */
    _tryNotifyRecovered(){
      if (this.state !== PlayerState.RECONNECTING || !this._reconnect.inFlight()) return;
      if (!this._isConnected()) return;
      this._reconnect.notifyRecovered();
    }

    // ------------
    // Recovery API (called by event handlers & watchdog)
    // ------------

    requestRecovery(reason, severity){
      if (!this.cfg.autonomousEnabled) return;
      if (!this.desiredPlaying) return;
      if (this.state === PlayerState.ERROR) return;

      const r = String(reason || 'unknown');
      const sev = severity != null
        ? Math.max(RecoverySeverity.SOFT, Math.min(RecoverySeverity.HARD, severity))
        : defaultSeverityForReason(r, RecoverySeverity.SOFT);

      this.handleEvent({ type: EventType.RECONNECT_SCHEDULED, reason: r, severity: sev, generation: this._sessionToken });
    }

    /**
     * Handle a stream failure: if we are RECONNECTING and an attempt is in flight, end it now;
     * otherwise request recovery (schedule or merge into pending).
     */
    _handleStreamFailure(reason, severity){
      if (this.state === PlayerState.RECONNECTING && this._reconnect.inFlight()) {
        this._reconnect.notifyAttemptFailed(reason, severity);
      } else {
        this.requestRecovery(reason, severity);
      }
    }

    async _executeRecoveryAttempt(ctx){
      const token = ctx.token;
      if (this._dropIfStale(token, 'recovery_attempt')) return;

      this.handleEvent({ type: EventType.RECOVERY_ATTEMPT_STARTED, generation: token });

      // Clean up per-attempt timers that could immediately retrigger.
      this._clearIceGrace();
      this._clearTrackMuteTimers();

      // Execute ladder action (side-effecting).
      if (ctx.action === AP.Core.RecoveryAction.SOFT_RESTART) {
        await this.streaming.stop();
      } else if (ctx.action === AP.Core.RecoveryAction.REATTACH_PLUGIN) {
        await this.streaming.detach();
      } else {
        this._reconnect.expectSessionResetFromRecreate();
        await this.streaming.recreate(this.rtcConfig);
      }

      if (this._dropIfStale(token, 'recovery_attempt')) return;

      // Re-establish watch + bind stream.
      await this._reestablishWatch(token);
      if (this._dropIfStale(token, 'recovery_attempt')) return;

      // Ensure watchdog runs in all active modes.
      this._startWatchdog();
    }

    // ------------
    // ICE grace
    // ------------

    _armIceGrace(){
      if (this._timers.has('iceGrace')) return;
      this._timers.set('iceGrace', () => {
        if (this.iceState === 'disconnected') {
          const snapshot = this._buildPolicySnapshot();
          const decision = ConnectionPolicy.decide(DomainEventType.ICE_DISCONNECTED_GRACE_TIMEOUT, snapshot);
          this._applyPolicyDecision(decision);
        }
      }, this.cfg.iceDisconnectedGraceMs);
    }

    _clearIceGrace(){
      this._timers.clear('iceGrace');
    }

    // ------------
    // Track mute
    // ------------

    _armTrackMuteTimer(trackId){
      const id = String(trackId || '');
      if (!id) return;
      const key = 'trackMute:' + id;
      if (this._timers.has(key)) return;
      this._timers.set(key, () => {
        const snapshot = this._buildPolicySnapshot();
        const decision = ConnectionPolicy.decide(DomainEventType.TRACK_MUTE_TIMEOUT, snapshot);
        this._applyPolicyDecision(decision);
      }, this.cfg.trackMuteRestartMs);
    }

    _disarmTrackMuteTimer(trackId){
      this._timers.clear('trackMute:' + String(trackId || ''));
    }

    _clearTrackMuteTimers(){
      this._timers.clearPrefix('trackMute:');
    }

    // ------------
    // ERROR auto-recovery
    // ------------

    /**
     * Schedule auto-retry from ERROR state after exponential backoff.
     * Ensures full autonomy: player self-recovers even after reconnect exhaustion.
     * Delay: errorAutoRetryBaseMs * 2^count, capped at errorAutoRetryMaxMs.
     */
    _scheduleErrorAutoRetry(){
      if (!this.cfg.autonomousEnabled) return;
      if (this._errorRetryTimer != null) return;

      const base = this.cfg.errorAutoRetryBaseMs || 10000;
      const max = this.cfg.errorAutoRetryMaxMs || 120000;
      const delay = Math.min(base * Math.pow(2, this._errorRetryCount), max);
      this._errorRetryCount++;

      this.log.info('error_auto_retry_scheduled', { delay, count: this._errorRetryCount });

      this._errorRetryTimer = this.clock.setTimeout(() => {
        this._errorRetryTimer = null;
        if (this.state !== PlayerState.ERROR) return;
        this.log.info('error_auto_retry_firing', { count: this._errorRetryCount });
        this.retry();
      }, delay);
    }

    _clearErrorAutoRetry(){
      if (this._errorRetryTimer != null) {
        this.clock.clearTimeout(this._errorRetryTimer);
        this._errorRetryTimer = null;
      }
    }

    // ------------
    // Rendering
    // ------------

    _buildDebugText(){
      const now = this.clock.nowMs();
      const age = this._watchdog.getLastFrameAgeMs(now);
      const pending = this._reconnect.pending();
      const timers = (typeof this.clock.debugSnapshot === 'function') ? this.clock.debugSnapshot() : null;

      const lines = [
        `token: ${this._sessionToken}`,
        `state: ${this.state}`,
        `desired: ${this.desiredPlaying}`,
        `streamId: ${this.streamId}`,
        `ice: ${this.iceState}`,
        `webrtcUp: ${this.webrtcUp}`,
        `firstFrame: ${this._firstFrameLatch}`,
        `connected: ${this._isConnected()}`,
        `degraded: ${this._degraded}`,
        `lastFrameAge: ${age} ms`,
        `reconnectAttempt: ${this._reconnect.attempt()}`,
        `pending: ${pending ? `${pending.reason} sev=${pending.severity}` : 'none'}`,
        `recoveryInFlight: ${this._reconnect.inFlight()}`,
        `errorRetryCount: ${this._errorRetryCount}`,
      ];
      if (timers) {
        lines.push(`timers: timeouts=${timers.timeouts} intervals=${timers.intervals}`);
      }
      return lines.join('\n');
    }

    _render(extra){
      this.ui.render({
        state: this.state,
        attempt: this._reconnect.attempt() || 0,
        desiredPlaying: this.desiredPlaying,
        errCode: this.errCode,
        autoplayBlocked: !!(extra && extra.autoplayBlocked),
        debugText: this._buildDebugText(),
      });
    }

    /**
     * Full teardown for SPA navigation / hot-reload. Removes listeners, stops all timers,
     * stops streaming, and nullifies references. After destroy(), the controller is inert.
     */
    destroy(){
      // Stop everything
      this.desiredPlaying = false;
      this._stopAll('destroy');
      this.streaming.stop().catch(() => {});

      // Remove visibility listener
      if (typeof document !== 'undefined') {
        document.removeEventListener('visibilitychange', this._boundOnVisibilityChange);
      }

      // Stop frame clock
      if (this.ui && typeof this.ui.stopFrameClock === 'function') this.ui.stopFrameClock();

      // Stop stats
      if (this.stats) this.stats.stop();

      // Clear ring buffer
      if (this._eventRingBuffer) this._eventRingBuffer.clear();

      // Clear global reference
      if (window.autonomousPlayerController === this) window.autonomousPlayerController = null;

      this.log.info('destroyed', { token: this._sessionToken });
    }
  }

  AP.App.PlayerController = PlayerController;
})();
