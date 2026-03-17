(function(){
  'use strict';
  const AP = window.AutonomousPlayer;
  if (!AP) throw new Error('AutonomousPlayer namespace missing');

  class JoystickService {
    /**
     * @param {any} cfg
     * @param {any} logger
     * @param {any} textroomAdapter
     */
    constructor(cfg, logger, textroomAdapter){
      this.cfg = cfg;
      this.log = logger;
      this.textroom = textroomAdapter;
      this._active = () => false;
      this._httpEnabled = !!(cfg.joystickHttp || window.location.search.includes('joy_http=1'));
      this._started = false;
      this._httpInFlight = false;

      // ── Joystick e2e latency measurement ──
      this._pingId = Date.now();   // session-unique base: always > previous session's IDs
      this._lastPongId = -1;
      this._joyE2eMs = NaN;
      this._joyJitterMs = NaN;      // EMA of |sample - smoothed| (mean absolute deviation)
      this._clockOffset = 0;        // server_ms - browser_ms (estimated)
      this._clockSynced = false;
      this._bestRtt = Infinity;     // best RTT seen for clock-sync (NTP-style)
      this._pingTimer = null;
      this._pongTimer = null;
      this._clockSyncTimer = null;

      // ── Poll RTT tracking (NTP-style min-filter) ──
      this._bestPollRtt = Infinity; // minimum observed poll RTT
      this._pollRttEma = NaN;       // EMA of poll RTT (for spike detection)
      this._e2eSampleCount = 0;     // how many valid e2e samples we have

      // Backoff state for HTTP polls (reduce 502 spam during tunnel drops)
      this._pongDelay = 2000;
      this._clockDelay = 60000;
      this._POLL_MIN = 2000;
      this._POLL_MAX = 16000;
      this._CLOCK_MIN = 60000;
      this._CLOCK_MAX = 300000;
    }

    /** Current joystick e2e latency in ms, or NaN if not yet measured. */
    get joyE2eMs(){ return this._joyE2eMs; }

    /** Current joystick jitter (latency variation) in ms, or NaN if not yet measured. */
    get joyJitterMs(){ return this._joyJitterMs; }

    setActivePredicate(fn){
      this._active = typeof fn === 'function' ? fn : () => false;
    }

    async boot(){
      if (!this.cfg.joystickEnabled) {
        this.log.info('joystick_disabled', {});
        return;
      }
      if (this._started) return;
      this._started = true;

      let cfg = null;
      try {
        const resp = await fetch(`${this.cfg.restBase}/gamepad_config.json`, { cache: 'no-store' });
        if (resp.ok) {
          cfg = await resp.json();
          this.log.info('gamepad_cfg_loaded', {});
        } else {
          this.log.warn('gamepad_cfg_http_error', { status: resp.status });
        }
      } catch (err) {
        this.log.warn('gamepad_cfg_fetch_failed', { error: String(err?.message || err) });
      }

      this._startGamepadDriver(cfg);
      this._startE2eMeasurement();
    }

    // ── E2E latency measurement: clock sync + ping/pong ──

    _startE2eMeasurement(){
      // Clock-sync: lightweight background probes used only as
      // cross-validation and fallback for relays without served_ms.
      this._doClockSyncBurst(3);
      this._scheduleClockSync();

      // Send ping every 2s through TextRoom DataChannel
      this._pingTimer = setInterval(() => this._sendPing(), 2000);

      // Poll pong from relay (offset 1s from ping), with backoff on errors
      setTimeout(() => this._schedulePongPoll(), 1000);
    }

    _schedulePongPoll(){
      this._pongTimer = setTimeout(async () => {
        const ok = await this._pollPong();
        this._pongDelay = ok
          ? this._POLL_MIN
          : Math.min(this._pongDelay * 2, this._POLL_MAX);
        this._schedulePongPoll();
      }, this._pongDelay);
    }

    _scheduleClockSync(){
      this._clockSyncTimer = setTimeout(async () => {
        const ok = await this._doClockSync();
        this._clockDelay = ok
          ? this._CLOCK_MIN
          : Math.min(this._clockDelay * 2, this._CLOCK_MAX);
        this._scheduleClockSync();
      }, this._clockDelay);
    }

    async _doClockSyncBurst(n){
      for (let i = 0; i < n; i++) {
        await this._doClockSync();
        if (i < n - 1) await new Promise(r => setTimeout(r, 800));
      }
    }

    async _doClockSync(){
      try {
        const t1 = Date.now();
        const resp = await fetch(`${this.cfg.restBase}/relay/time`, { cache: 'no-store' });
        const t2 = Date.now();
        if (!resp.ok) return false;
        const data = await resp.json();
        const serverMs = data.server_ms;
        if (!Number.isFinite(serverMs)) return;

        const rtt = t2 - t1;
        const offset = serverMs - (t1 + t2) / 2;

        // NTP-style: only trust samples with low RTT (less jitter from
        // asymmetric tunnel latency). Accept if RTT ≤ 2× best seen,
        // capped at 1500ms absolute max. Always accept first 3 samples
        // or samples that beat or match the best RTT.
        const rttOk = rtt <= this._bestRtt * 2 && rtt <= 1500;
        const isBetter = rtt <= this._bestRtt;

        if (isBetter) {
          this._bestRtt = rtt;
        }

        if (!this._clockSynced) {
          // First sample — always accept
          this._clockOffset = offset;
          this._clockSynced = true;
          this.log.debug('clock_sync', { offset: Math.round(offset), rtt, best_rtt: rtt, accepted: true });
        } else if (isBetter) {
          // New best RTT — adopt immediately (most accurate sample)
          this._clockOffset = offset;
          this.log.debug('clock_sync', { offset: Math.round(offset), rtt, best_rtt: this._bestRtt, accepted: true });
        } else if (rttOk) {
          // Acceptable RTT — gentle EMA (α=0.1) to track slow drift
          this._clockOffset = 0.1 * offset + 0.9 * this._clockOffset;
          this.log.debug('clock_sync', { offset: Math.round(this._clockOffset), rtt, best_rtt: this._bestRtt, accepted: true });
        } else {
          // High RTT — discard (tunnel congestion / asymmetry)
          this.log.debug('clock_sync', { offset: Math.round(offset), rtt, best_rtt: this._bestRtt, accepted: false });
        }

        // Slowly relax best_rtt so we adapt if network baseline changes
        // (inflate by 5% each sync cycle, capped so it doesn't grow forever)
        if (this._bestRtt < 5000) {
          this._bestRtt = Math.min(this._bestRtt * 1.05, 5000);
        }
        return true;
      } catch (e) {
        this.log.debug('clock_sync_error', { error: String(e?.message || e) });
        return false;
      }
    }

    _sendPing(){
      if (!this.textroom || !this.textroom.ready) return;
      this._pingId++;
      this.textroom.sendPing(this._pingId);
    }

    async _pollPong(){
      try {
        // Measure poll HTTP RTT so we can estimate return-path latency
        const pollT1 = Date.now();
        const resp = await fetch(`${this.cfg.restBase}/relay/pong`, { cache: 'no-store' });
        const pollT2 = Date.now();
        if (!resp.ok) return false;
        const pong = await resp.json();
        if (!pong || pong.id == null || pong.id <= 0) return true;
        if (pong.id <= this._lastPongId) return true; // already processed

        // Stale pong from a previous session? browser_ts will be ancient.
        // Skip it and reset _lastPongId so future pongs from THIS session
        // (with higher IDs) are accepted.
        const age = pollT2 - pong.browser_ts;
        if (age > 30000 || age < 0) {
          this.log.debug('joy_e2e_stale_pong', { id: pong.id, age_s: (age / 1000).toFixed(1) });
          this._lastPongId = pong.id; // consume it so we don't reprocess
          return true;
        }

        this._lastPongId = pong.id;

        // ── RTT-based e2e (no clock-sync required) ──
        //
        // Timeline:
        //   browser_ts    → DataChannel → relay_rx_ms → queue → relay_fwd_ms
        //   …pong sits…   → served_ms (relay serves GET /pong) → browser (pollT2)
        //
        // joy_e2e = totalElapsed − pong_age − HTTP_return
        //         ≈ DC_forward + queue
        //
        // HTTP_return is estimated as bestPollRtt / 2 (NTP-style: the
        // minimum observed poll RTT has the least congestion noise and
        // gives the most stable one-way estimate, even through tunnels).

        const pollRtt = pollT2 - pollT1;

        // ── NTP-style poll RTT tracking ──
        const isBetter = pollRtt < this._bestPollRtt;
        if (isBetter) this._bestPollRtt = pollRtt;

        if (!Number.isFinite(this._pollRttEma)) {
          this._pollRttEma = pollRtt;
        } else {
          this._pollRttEma = 0.15 * pollRtt + 0.85 * this._pollRttEma;
        }

        // Relax bestPollRtt slowly so we adapt to network changes (5% per sample)
        if (!isBetter && this._bestPollRtt < 30000) {
          this._bestPollRtt = Math.min(this._bestPollRtt * 1.05, 30000);
        }

        // Spike gate: reject if pollRtt > 4× smoothed (tunnel congestion).
        // totalElapsed includes the same congested return path, so the
        // sample would not subtract cleanly → skip it entirely.
        const spikeThresh = Math.max(this._pollRttEma * 4, this._bestPollRtt * 6, 3000);
        if (pollRtt > spikeThresh && this._e2eSampleCount > 3) {
          this.log.debug('joy_e2e_spike_skip', {
            poll_rtt: pollRtt,
            thresh: Math.round(spikeThresh),
            best: Math.round(this._bestPollRtt),
          });
          return true; // not an error — just a noisy sample
        }

        const totalElapsed = pollT2 - pong.browser_ts;

        // served_ms is added by relay at GET time (not at pong creation)
        const hasServedMs = Number.isFinite(pong.served_ms);
        const pongAge = hasServedMs ? (pong.served_ms - pong.relay_fwd_ms) : 0;
        const queueMs = Math.max(0, pong.relay_fwd_ms - pong.relay_rx_ms);

        // Estimate HTTP one-way using best observed poll RTT (most stable).
        // For the first few samples, use raw pollRtt/2 until bestPollRtt converges.
        const httpOneWay = this._e2eSampleCount < 3
          ? pollRtt / 2
          : this._bestPollRtt / 2;

        let raw;
        if (hasServedMs) {
          // Primary: RTT-based (subtract pong dwell-time + stable HTTP return)
          raw = totalElapsed - pongAge - httpOneWay;
        } else if (this._clockSynced) {
          // Fallback: clock-sync path (for old relay without served_ms)
          const browserTsOnServerClock = pong.browser_ts + this._clockOffset;
          raw = (pong.relay_rx_ms - browserTsOnServerClock) + queueMs;
        } else {
          // No served_ms and no clock sync — use raw pollRtt/2
          raw = totalElapsed - pollRtt / 2;
        }

        // Add average relay→robot HTTP POST latency (measured server-side)
        const robotMs = pong.robot_avg_ms || 0;
        raw += robotMs;

        // Clamp (small negatives possible from HTTP asymmetry)
        const clamped = Math.max(0, raw);

        // ── Cross-validation with clock-sync estimate (diagnostic only) ──
        let clockEstimate = NaN;
        if (this._clockSynced && hasServedMs) {
          const browserTsOnServerClock = pong.browser_ts + this._clockOffset;
          clockEstimate = (pong.relay_rx_ms - browserTsOnServerClock) + queueMs + robotMs;
        }

        // EMA smoothing — faster α for first samples, then settle
        const alpha = this._e2eSampleCount < 5 ? 0.4 : 0.15;
        if (!Number.isFinite(this._joyE2eMs)) {
          this._joyE2eMs = clamped;
        } else {
          this._joyE2eMs = alpha * clamped + (1 - alpha) * this._joyE2eMs;
        }

        // Jitter: EMA of absolute deviation from smoothed E2E
        const deviation = Math.abs(clamped - this._joyE2eMs);
        if (!Number.isFinite(this._joyJitterMs)) {
          this._joyJitterMs = deviation;
        } else {
          this._joyJitterMs = alpha * deviation + (1 - alpha) * this._joyJitterMs;
        }

        this._e2eSampleCount++;

        this.log.debug('joy_e2e', {
          id: pong.id,
          e2e: Math.round(this._joyE2eMs),
          jitter: Math.round(this._joyJitterMs),
          raw: Math.round(raw),
          queue: Math.round(queueMs),
          robot: Math.round(robotMs),
          pong_age: Math.round(pongAge),
          poll_rtt: pollRtt,
          best_poll_rtt: Math.round(this._bestPollRtt),
          clock_est: Number.isFinite(clockEstimate) ? Math.round(clockEstimate) : null,
        });
        return true;
      } catch (e) {
        this.log.debug('pong_poll_error', { error: String(e?.message || e) });
        return false;
      }
    }

    async ensureTextRoomAttached(){
      if (!this.cfg.textroomEnabled) return;
      if (!this.textroom) return;
      try {
        if (!this.textroom.ready) {
          await this.textroom.attach();
          this.log.info('textroom_ready', {});
        }
      } catch (e) {
        // Not fatal; we can keep using HTTP transport.
        this.log.warn('textroom_attach_failed', { error: String(e?.message || e) });
      }
    }

    _startGamepadDriver(gamepadCfg){
      if (!window.GamepadDriver) {
        this.log.warn('gamepad_driver_missing', {});
        return;
      }
      const httpEnabled = this._httpEnabled;
      this.log.info('joystick_transport', { http: httpEnabled, textroom: !!this.cfg.textroomEnabled });

      window.GamepadDriver.start({
        debug: this.cfg.debug,
        intervalMs: 30,
        keepaliveMs: 30,
        ttlMs: 200,
        axesMap: Array.isArray(gamepadCfg?.axesMap) ? gamepadCfg.axesMap : undefined,
        buttonsMap: Array.isArray(gamepadCfg?.buttonsMap) ? gamepadCfg.buttonsMap : undefined,
        onFrame: (frame) => {
          if (!this._active()) return;
          if (httpEnabled) this._sendJoystickHttp(frame);
          if (this.textroom && this.textroom.ready) this.textroom.sendFrame(frame);
        },
      });
    }

    async _sendJoystickHttp(frame){
      if (this._httpInFlight) return; // drop frame if previous request still pending
      this._httpInFlight = true;
      try {
        const resp = await fetch(`${this.cfg.robotRest}/joystick/frame`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(frame),
          keepalive: true,
        });
        if (!resp.ok) this.log.warn('joystick_http_non_2xx', { status: resp.status });
      } catch (err) {
        this.log.warn('joystick_http_failed', { error: String(err?.message || err) });
      } finally {
        this._httpInFlight = false;
      }
    }
  }

  AP.App.JoystickService = JoystickService;
})();
