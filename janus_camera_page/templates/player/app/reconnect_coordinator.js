(function(){
  'use strict';
  const AP = window.AutonomousPlayer;
  if (!AP) throw new Error('AutonomousPlayer namespace missing');

  const computeBackoffMs = AP.Core.computeBackoffMs;
  const decideRecoveryAction = AP.Core.decideRecoveryAction;

  /**
   * ReconnectCoordinator
   *
   * Single owner for reconnect scheduling + attempt counters + escalation ladder.
   * Keeps PlayerController small: controller decides STATE transitions and executes the concrete IO.
   *
   * Reconnect timers are owned by RECONNECTING state (L14). Leaving RECONNECTING (e.g. to IDLE or ERROR)
   * cancels all timers via CANCEL_ALL_TIMERS → controller _stopAll → this.reset() (L15).
   * Reconnect start is only allowed when state is RECONNECTING; duplicate request() while already
   * scheduled or in-flight is ignored (latch, L11).
   *
   * Contract:
   * - Controller calls request(reason, severity) when a failure is detected (ICE failed, hangup, no frames, etc.).
   * - Controller calls startSettleWindow() when the WebRTC attempt has started (e.g. streaming offer
   *   received). The settle window (connectSettleMs) runs from that moment, not from executeAttempt().
   * - Controller calls notifyRecovered() when recovery condition is met (webrtc up + data plane healthy).
   *   If notifyRecovered() is not called within connectSettleMs of startSettleWindow(), the attempt
   *   is treated as failed. If startSettleWindow() is not called within settleStartTimeoutMs of
   *   executeAttempt() returning, the attempt is also treated as failed.
   *
   * App-layer component: uses Core pure functions (backoff + action decision).
   */
  class ReconnectCoordinator {
    /**
     * @param {any} cfg
     * @param {any} clock
     * @param {any} logger
     */
    constructor(cfg, clock, logger){
      this.cfg = cfg;
      this.clock = clock;
      this.log = logger;

      this._attempt = 0; // one-based for logs/UI
      this._pending = null; // {reason, severity}
      // L14: timers below are owned by RECONNECTING; L15: leaving RECONNECTING cancels all via reset()/_clearTimers(). E3: no timer can resurrect state after IDLE/ERROR.
      this._scheduleTimer = null;   // backoff timer: next attempt run
      this._settleStartTimeoutTimer = null; // max wait for startSettleWindow() after executeAttempt
      this._settleTimer = null;     // settle window: wait for notifyRecovered()
      this._inFlight = false;
      this._tokenAtSchedule = 0;
      this._expectedSessionResetCount = 0;

      // injected context from controller
      this._ctx = {
        getToken: () => 0,
        shouldContinue: () => false,
        isRecovered: () => false,
        // executeAttempt({attempt, action, reason, severity, token}) -> Promise<void>
        executeAttempt: async () => {},
        onScheduled: () => {},
        onAttempt: () => {},
        onSuccess: () => {},
        onExhausted: () => {},
      };
    }

    bindContext(ctx){
      this._ctx = Object.assign({}, this._ctx, ctx || {});
    }

    attempt(){ return this._attempt; }
    pending(){ return this._pending; }
    inFlight(){ return this._inFlight; }

    /**
     * When tab becomes visible again: if we are RECONNECTING but had stopped scheduling (e.g. shouldContinue was false
     * while tab was hidden), schedule the next attempt. No-op if already in-flight or timer set.
     */
    resumeIfPending(){
      if (this._inFlight || this._scheduleTimer) return;
      if (this._pending && this._ctx.shouldContinue()) this._scheduleNext();
    }

    /**
     * Escalate the severity of the current pending request (e.g. after long tab-hide the Janus session
     * is likely dead, so we want RECREATE_SESSION immediately). No-op if nothing is pending.
     * @param {number} severity
     */
    escalateSeverity(severity){
      if (!this._pending) return;
      this._pending.severity = Math.max(this._pending.severity, Number(severity) || 1);
    }

    /**
     * Called by the controller when recovery condition is met (webrtc up + data plane healthy).
     * If we are waiting after an attempt (settle window), completes successfully and clears state.
     * Idempotent if not in that state.
     */
    notifyRecovered(){
      if (!this._inFlight || !this._settleTimer) return;

      this.clock.clearTimeout(this._settleTimer);
      this._settleTimer = null;
      this.log.info('reconnect_success', { attempt: this._attempt, token: this._ctx.getToken(), event: true });
      const used = this._attempt;
      this._attempt = 0;
      this._pending = null;
      this._inFlight = false;
      this._clearTimers();
      try { this._ctx.onSuccess({ attempt: used, token: this._ctx.getToken() }); } catch(_) {}
    }

    /**
     * Called by the controller when a definite failure occurred during the current attempt
     * (e.g. ICE failed, hangup, error). Ends the attempt immediately and schedules the next;
     * no need to wait for the settle timer.
     * @param {string} [reason]
     * @param {number} [severity]
     */
    notifyAttemptFailed(reason, severity){
      if (!this._inFlight) return;
      if (reason != null) {
        const r = String(reason || 'unknown');
        const sev = Number(severity) || 1;
        if (!this._pending) this._pending = { reason: r, severity: sev };
        else {
          this._pending.reason = r || this._pending.reason;
          this._pending.severity = Math.max(this._pending.severity, sev);
        }
      }
      this._clearTimers();
      this._inFlight = false;
      this._scheduleNext();
    }

    /**
     * Call before streaming.recreate() so that the next SESSION_RESET(s) from our own destroy/recreate
     * are ignored (not treated as attempt failed). One recreate() yields two SESSION_RESET events:
     * SESSION_DESTROYED from our destroy(), SESSION_RECREATED after init().
     */
    expectSessionResetFromRecreate(){
      this._expectedSessionResetCount = 2;
    }

    /**
     * When controller receives SESSION_RESET while RECONNECTING and inFlight: call this first.
     * @returns {boolean} true if this reset was expected (consumed); then do not call notifyAttemptFailed.
     */
    consumeExpectedSessionReset(){
      if (this._expectedSessionResetCount > 0) {
        this._expectedSessionResetCount -= 1;
        return true;
      }
      return false;
    }

    /**
     * Called by the controller when the WebRTC attempt has actually started (e.g. streaming offer received).
     * Starts the settle window (connectSettleMs) from this moment instead of from executeAttempt() return.
     * Idempotent: repeated calls in the same attempt do not restart the settle timer.
     */
    startSettleWindow(){
      if (!this._inFlight || this._settleTimer) return;

      if (this._settleStartTimeoutTimer) {
        this.clock.clearTimeout(this._settleStartTimeoutTimer);
        this._settleStartTimeoutTimer = null;
      }
      const token = this._ctx.getToken();
      this._settleTimer = this.clock.setTimeout(() => {
        this._settleTimer = null;
        this._onSettle(token);
      }, this.cfg.connectSettleMs);
    }

    /** L15: Cancel all timers when leaving RECONNECTING (IDLE/ERROR). E3: no timer can fire after this. */
    reset(){
      this._clearTimers();
      this._attempt = 0;
      this._pending = null;
      this._inFlight = false;
      this._tokenAtSchedule = 0;
    }

    /** Clear _scheduleTimer, _settleStartTimeoutTimer, _settleTimer. Safe to call repeatedly. */
    _clearTimers(){
      if (this._scheduleTimer) {
        this.clock.clearTimeout(this._scheduleTimer);
        this._scheduleTimer = null;
      }
      if (this._settleStartTimeoutTimer) {
        this.clock.clearTimeout(this._settleStartTimeoutTimer);
        this._settleStartTimeoutTimer = null;
      }
      if (this._settleTimer) {
        this.clock.clearTimeout(this._settleTimer);
        this._settleTimer = null;
      }
    }

    /**
     * Request recovery (idempotent & coalescing).
     * ALWAYS records _pending (reason + severity) so that a later resumeIfPending() can
     * pick it up — e.g. when the tab was hidden at the time of the request.
     * Scheduling only happens when shouldContinue() is true.
     * @param {string} reason
     * @param {number} severity
     */
    request(reason, severity){
      const r = String(reason || 'unknown');
      const sev = Number(severity) || 1;

      // Always record intent — even when scheduling is blocked (tab hidden).
      if (!this._pending) {
        this._pending = { reason: r, severity: sev };
      } else {
        // Keep max severity; keep latest reason.
        this._pending.severity = Math.max(this._pending.severity, sev);
        this._pending.reason = r || this._pending.reason;
      }

      // Only schedule when predicate allows (tab visible, autonomous, desired, not ERROR).
      if (!this._ctx.shouldContinue()) return;

      // Already scheduled or running (latch: at most one reconnect in progress).
      if (this._inFlight || this._scheduleTimer) {
        this.log.debug('RECONNECT_ABORTED', {
          reason: this._inFlight ? 'in_flight' : 'already_scheduled',
          token: this._ctx.getToken(),
        });
        return;
      }

      this._scheduleNext();
    }

    _scheduleNext(){
      if (!this._pending) return;
      if (!this._ctx.shouldContinue()) return;

      if (this._attempt >= this.cfg.maxReconnectAttempts) {
        this.log.error('reconnect_exhausted', { attempt: this._attempt, reason: this._pending.reason, severity: this._pending.severity, token: this._ctx.getToken() });
        this._ctx.onExhausted(this._pending);
        return;
      }

      const nextAttempt = this._attempt + 1;
      const token = this._ctx.getToken();
      this._tokenAtSchedule = token;
      const delay = computeBackoffMs(nextAttempt, this.cfg, token);

      this.log.warn('reconnect_scheduled', {
        attempt: nextAttempt,
        delay,
        reason: this._pending.reason,
        severity: this._pending.severity,
        token,
      });

      try { this._ctx.onScheduled({ attempt: nextAttempt, delay, reason: this._pending.reason, severity: this._pending.severity, token }); } catch(_) {}

      this._scheduleTimer = this.clock.setTimeout(() => this._runScheduled(token), delay);
    }

    async _runScheduled(token){
      // timer fired
      if (this._scheduleTimer) {
        this.clock.clearTimeout(this._scheduleTimer);
        this._scheduleTimer = null;
      }

      if (!this._ctx.shouldContinue()) return;
      if (token !== this._ctx.getToken()) {
        this.log.debug('EVENT_DROPPED', { stale: true, context: 'reconnect_schedule', token, current: this._ctx.getToken() });
        return;
      }
      if (this._inFlight) return;

      // Recovered in the meantime (e.g. first frame arrived after we scheduled); treat as success.
      if (this._ctx.isRecovered()) {
        this.log.info('reconnect_success', { attempt: this._attempt, token, skipped: true });
        const used = this._attempt;
        this._attempt = 0;
        this._pending = null;
        this._clearTimers();
        try { this._ctx.onSuccess({ attempt: used, token }); } catch(_) {}
        return;
      }

      // Health check: verify Janus is reachable before consuming an attempt
      if (this.cfg.healthCheckBeforeReconnect && this.cfg.healthCheckUrl) {
        try {
          const ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
          const hcTimeout = ctrl ? this.clock.setTimeout(() => ctrl.abort(), this.cfg.healthCheckTimeoutMs || 3000) : null;
          const resp = await (typeof fetch !== 'undefined' ? fetch : globalThis.fetch)(
            this.cfg.healthCheckUrl, { signal: ctrl?.signal }
          );
          if (hcTimeout != null) this.clock.clearTimeout(hcTimeout);
          if (!resp.ok) throw new Error(`healthz ${resp.status}`);
          const body = await resp.json();
          if (!body.ok) throw new Error('healthz not ok');
        } catch (e) {
          this.log.warn('reconnect_health_check_failed', { error: String(e?.message || e), attempt: this._attempt + 1, token });
          // Do NOT consume an attempt — reschedule with current backoff
          this._scheduleNext();
          return;
        }
      }

      this._inFlight = true;
      this._attempt += 1;

      const pending = this._pending || { reason: 'unknown', severity: 1 };
      const action = decideRecoveryAction(this._attempt, pending.severity, this.cfg);

      const nextDelayMs = this._attempt + 1 <= this.cfg.maxReconnectAttempts ? computeBackoffMs(this._attempt + 1, this.cfg, token) : null;
      this.log.info('reconnect_attempt', {
        attempt: this._attempt,
        action,
        reason: pending.reason,
        severity: pending.severity,
        token,
        delayMs: nextDelayMs,
      });

      try { this._ctx.onAttempt({ attempt: this._attempt, action, reason: pending.reason, severity: pending.severity, token }); } catch(_) {}

      const attemptCtx = { attempt: this._attempt, action, reason: pending.reason, severity: pending.severity, token };
      const timeoutMs = this.cfg.reconnectAttemptTimeoutMs || 15000;
      let timeoutId = null;
      const timeoutPromise = new Promise((_, reject) => {
        timeoutId = this.clock.setTimeout(() => reject(new Error('reconnect_attempt_timeout')), timeoutMs);
      });

      try {
        await Promise.race([
          this._ctx.executeAttempt(attemptCtx),
          timeoutPromise,
        ]);
        if (timeoutId != null) {
          this.clock.clearTimeout(timeoutId);
          timeoutId = null;
        }

        if (this._settleStartTimeoutTimer) {
          this.clock.clearTimeout(this._settleStartTimeoutTimer);
          this._settleStartTimeoutTimer = null;
        }

        // If startSettleWindow() was already called during executeAttempt (e.g. offer/460 same-tick), do not start the wait.
        if (this._settleTimer) return;

        // Wait for startSettleWindow() (offer received); if not called within settleStartTimeoutMs, treat as failed.
        const settleStartTimeoutMs = this.cfg.settleStartTimeoutMs ?? 5000;
        this._settleStartTimeoutTimer = this.clock.setTimeout(() => {
          this._settleStartTimeoutTimer = null;
          if (this._settleTimer) return; // startSettleWindow() was already called
          if (token !== this._ctx.getToken()) return; // stale timer from previous cycle
          if (!this._ctx.shouldContinue()) { this._inFlight = false; return; }
          this.log.debug('reconnect_settle_start_timeout', { attempt: this._attempt, token });
          // Offer never arrived — escalate to HARD so next attempt skips to RECREATE_SESSION.
          if (this._pending) {
            this._pending.severity = Math.max(this._pending.severity, 3); // RecoverySeverity.HARD
          }
          this._inFlight = false;
          this._scheduleNext();
        }, settleStartTimeoutMs);
      } catch (e) {
        if (timeoutId != null) this.clock.clearTimeout(timeoutId);
        if (this._settleStartTimeoutTimer) {
          this.clock.clearTimeout(this._settleStartTimeoutTimer);
          this._settleStartTimeoutTimer = null;
        }
        const errMsg = String(e?.message || e);
        this.log.warn('reconnect_attempt_failed', { attempt: this._attempt, error: errMsg, token });
        // On timeout, escalate to HARD so next attempt skips straight to RECREATE_SESSION.
        if (errMsg === 'reconnect_attempt_timeout' && this._pending) {
          this._pending.severity = Math.max(this._pending.severity, 3); // RecoverySeverity.HARD
        }
        this._inFlight = false;
        this._scheduleNext();
      }
    }

    _onSettle(token){
      if (!this._ctx.shouldContinue()) {
        this._inFlight = false;
        return;
      }
      if (token !== this._ctx.getToken()) {
        this.log.debug('EVENT_DROPPED', { stale: true, context: 'reconnect_settle', token, current: this._ctx.getToken() });
        this._inFlight = false;
        return;
      }
      // Settle timer fired without notifyRecovered() — treat as not recovered.
      // Escalate to HARD: if settle failed, soft recovery isn't working.
      if (this._pending) {
        this._pending.severity = Math.max(this._pending.severity, 3); // RecoverySeverity.HARD
      }
      this._inFlight = false;
      this._scheduleNext();
    }
  }

  AP.App.ReconnectCoordinator = ReconnectCoordinator;
})();
