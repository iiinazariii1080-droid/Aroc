(function(){
  'use strict';
  const AP = window.AutonomousPlayer;
  if (!AP) throw new Error('AutonomousPlayer namespace missing');

  /**
   * Janus Streaming plugin adapter.
   * @implements {AP.Ports.StreamingPort}
   * Infrastructure adapter: owns the streaming plugin handle (but NOT the session).
   * Session lifecycle is delegated to JanusSessionManager.
   * One handle epoch: _handleGen is bumped on detach/session reset; all callbacks check gen so
   * stale events from previous handles are ignored (no double create/destroy).
   */
  class JanusStreamingAdapter {
    /**
     * @param {any} cfg
     * @param {any} logger
     * @param {any} sessionManager
     */
    constructor(cfg, logger, sessionManager, clock){
      this.cfg = cfg;
      this.log = logger;
      this.session = sessionManager;
      this.clock = clock || { setTimeout: (fn, ms) => window.setTimeout(fn, ms), clearTimeout: (id) => window.clearTimeout(id) };
      if (!this.session) throw new Error('JanusSessionManager required');

      this._eventSink = null;

      this.handle = null;
      this._handleGen = 0;

      this._inboundStream = new MediaStream();
      this._tracksByMid = new Map();

      this._ensurePromise = null;
      /** Resolve/reject for the in-flight watch() so we resolve only when the substantive response (offer or 460) is in. */
      this._pendingWatch = null;
      /** Promise resolved when oncleanup fires for current handle (or on detach). Used so stop() waits for full teardown. */
      this._cleanupPromise = null;
      this._cleanupResolve = null;
      /** Serialize IO: only one of init/stop/watch/detach/recreate runs at a time. */
      this._io = Promise.resolve();
      /** Generation counter for recreate; stale enqueued operations check this and bail out. */
      this._ioGen = 0;

      /** DEF-07: request ICE restart on next createAnswer after session recreate. */
      this._wantIceRestart = false;

      // Invalidate handle if session is destroyed/recreated.
      this._unsubSession = this.session.onEvent((ev) => {
        if (!ev || !ev.type) return;
        if (ev.type === 'SESSION_DESTROYED' || ev.type === 'SESSION_RECREATED') {
          if (this._pendingWatch) {
            this._pendingWatch.reject(new Error('Session reset'));
            this._pendingWatch = null;
          }
          if (this._cleanupResolve) {
            this._cleanupResolve();
            this._cleanupResolve = null;
          }
          this._handleGen += 1;
          this.handle = null;
          this._clearAllTracks();
          this._emit('SESSION_RESET', { type: ev.type });
        }
      });
    }

    setEventSink(sink, getToken){
      this._eventSink = sink;
      this._getToken = typeof getToken === 'function' ? getToken : null;
    }

    _emit(type, payload){
      try {
        const token = this._getToken ? this._getToken() : undefined;
        this._eventSink && this._eventSink({ type, payload, token });
      } catch (e) {
        this.log.warn('event_sink_error', { type, error: String(e) });
      }
    }

    /** Clear all inbound tracks, detach event handlers, and reset the MediaStream. */
    _clearAllTracks(){
      this._tracksByMid.clear();
      try {
        for (const tr of this._inboundStream.getTracks()) {
          try { tr.onmute = null; tr.onunmute = null; tr.onended = null; } catch(_) {}
          try { tr.stop && tr.stop(); } catch(_) {}
        }
      } catch(_) {}
      this._inboundStream = new MediaStream();
    }

    /** Returns true if handle generation is stale (callback from previous handle). When true, logs EVENT_DROPPED. */
    _dropIfStaleGen(gen, context){
      if (gen === this._handleGen) return false;
      this.log.debug('EVENT_DROPPED', { stale: true, context, gen, current: this._handleGen });
      return true;
    }

    /** Enqueue an async operation so only one IO method runs at a time. */
    _enqueue(fn){
      this._io = this._io.then(fn).catch((err) => {
        this.log.warn('enqueue_error', { error: String(err), stack: err && err.stack });
      });
      return this._io;
    }

    getInboundStream(){
      return this._inboundStream;
    }

    getPeerConnection(){
      const h = this.handle;
      return h && h.webrtcStuff ? h.webrtcStuff.pc : null;
    }

    /**
     * Returns true if the underlying Janus session WebSocket is still connected.
     * Used to short-circuit SOFT_RESTART / REATTACH when the server is unreachable.
     */
    isSessionAlive(){
      return this.session && typeof this.session.isAlive === 'function' && this.session.isAlive();
    }

    async init(rtcConfig){
      return this._enqueue(() => this._ensureSessionAndHandle(rtcConfig));
    }

    async ensureReady(){
      if (!this.handle) throw new Error('Janus streaming handle not attached');
    }

    async recreate(rtcConfig){
      const gen = ++this._ioGen;
      // Reject any pending watch promise from stale attempts.
      if (this._pendingWatch) {
        this._pendingWatch.reject(new Error('Session recreated'));
        this._pendingWatch = null;
      }
      // Flush stale queued operations from timed-out attempts.
      // _handleGen guards against stale attach callbacks corrupting state.
      // NEW-04: Clear stale ensure promise to prevent orphaned waits
      this._ensurePromise = null;
      this._io = Promise.resolve();
      return this._enqueue(async () => {
        if (this._ioGen !== gen) return;
        await this._detachHandle();
        if (this._ioGen !== gen) return;
        this._wantIceRestart = true;
        await this.session.recreate(rtcConfig);
        if (this._ioGen !== gen) return;
        await this._ensureSessionAndHandle(rtcConfig);
      });
    }

    async detach(){
      // NEW-02: Clean up session listener on detach to prevent leak
      if (this._unsubSession) {
        this._unsubSession();
        this._unsubSession = null;
      }
      return this._enqueue(async () => {
        await this._detachHandle();
        await this._ensureSessionAndHandle(null);
      });
    }

    /**
     * Stop the current stream. Idempotent: no-op when there is no handle (e.g. after detach or multiple calls).
     * All I/O (stop, init, watch, detach, recreate) is serialized via _enqueue.
     */
    async stop(){
      // Unsubscribe from session events to prevent listener accumulation.
      if (this._unsubSession) {
        this._unsubSession();
        this._unsubSession = null;
      }
      if (!this.handle) return Promise.resolve();
      return this._enqueue(async () => {
      const h = this.handle;
      if (!h) return;
      const gen = this._handleGen;
      const cleanupPromise = this._cleanupPromise;
      await new Promise((resolve) => {
        try {
          h.send({ message: { request: 'stop' }, success: () => resolve(true), error: () => resolve(true) });
        } catch (_) {
          resolve(true);
        }
      });
      if (!cleanupPromise) return;
      const CLEANUP_TIMEOUT_MS = 8000;
      const timeoutPromise = new Promise((resolve) => {
        this.clock.setTimeout(() => resolve(), CLEANUP_TIMEOUT_MS);
      });
      await Promise.race([cleanupPromise, timeoutPromise]);
      if (gen !== this._handleGen) {
        this.log.debug('stop_cleanup_skipped', { reason: 'gen_changed', gen, current: this._handleGen });
      }
      });
    }

    /**
     * Full teardown: stop + null all references (R3-03, R3-04).
     * Call this when the adapter will never be reused.
     */
    async destroy(){
      await this.stop();
      this._eventSink = null;
      this._getToken = null;
      this._inboundStream = null;
      this.session = null;
    }

    async listStreams(){
      return this._enqueue(async () => {
        const h = this.handle;
        if (!h) throw new Error('No handle');
        return await new Promise((resolve, reject) => {
          h.send({
            message: { request: 'list' },
            success: (result) => resolve((result && result.list) ? result.list : []),
            error: (e) => reject(e),
          });
        });
      });
    }

    async watch(streamId){
      return this._enqueue(async () => {
      const h = this.handle;
      if (!h) throw new Error('No handle');
      const id = Number(streamId);
      if (!Number.isFinite(id)) throw new Error('Invalid streamId');

      return await new Promise((resolve, reject) => {
        this._pendingWatch = { resolve, reject };
        try {
          h.send({
            message: { request: 'watch', id },
            success: () => {
              // Do not resolve here; resolve when onmessage delivers the substantive response (offer or 460).
            },
            error: (e) => {
              const code = e && (e.error_code ?? e.errorCode);
              const msg = e && (e.error || e.message || '');
              const errObj = (typeof e === 'object' && e !== null) ? e : { error: e, error_code: code };
              if (code === 460 || (typeof msg === 'string' && msg.includes('Already watching'))) {
                this._emit('ERROR', { where: 'watch', error: errObj, error_code: 460 });
                if (this._pendingWatch) {
                  this._pendingWatch.reject(Object.assign({ error_code: 460, error: msg || 'Already watching' }, errObj));
                  this._pendingWatch = null;
                }
                return;
              }
              this._emit('ERROR', { where: 'watch', error: errObj, error_code: code });
              if (this._pendingWatch) {
                this._pendingWatch.reject(e);
                this._pendingWatch = null;
              }
            },
          });
        } catch (e) {
          if (this._pendingWatch) {
            this._pendingWatch.reject(e);
            this._pendingWatch = null;
          }
        }
      });
      });
    }

    // -----------------
    // Internals
    // -----------------

    async _ensureSessionAndHandle(rtcConfig){
      if (this._ensurePromise) return this._ensurePromise;
      this._ensurePromise = (async () => {
        await this.session.init(rtcConfig);
        if (!this.handle) await this._attachStreaming();
      })().finally(() => {
        this._ensurePromise = null;
      });
      return this._ensurePromise;
    }

    async _attachStreaming(){
      // Capture gen before attach so all callbacks (onmessage, etc.) see the same epoch; avoids races if attach completes after another detach.
      const gen = ++this._handleGen;
      const that = this;
      this._cleanupPromise = new Promise((resolve) => { this._cleanupResolve = resolve; });

      const handle = await this.session.attach('janus.plugin.streaming', {
        opaqueId: `streaming-${Janus.randomString(8)}`,
        onmessage: (msg, jsep) => {
          if (that._dropIfStaleGen(gen, 'handle_onmessage')) return;
          let offerEmitted = false;
          if (jsep) {
            // NEW-03: Don't emit STREAMING_OFFER_RECEIVED here — moved to _onJsepOffer after SDP validation
            that._onJsepOffer(gen, jsep);
            offerEmitted = true;
          }
          const errCode = msg && (msg.error_code ?? msg.errorCode);
          const errMsg = msg && (msg.error || msg.error_message || '');
          const is460 = errCode === 460 || (typeof errMsg === 'string' && errMsg.includes('Already watching'));
          if (is460) {
            that._emit('ERROR', { where: 'watch', error: msg || errMsg, error_code: 460 });
            if (that._pendingWatch) {
              that._pendingWatch.reject(Object.assign({ error_code: 460, error: errMsg || 'Already watching' }, msg || {}));
              that._pendingWatch = null;
            }
          }
          const isWatchResult = msg && msg.result !== undefined && !msg.list;
          if (isWatchResult && !is460 && !offerEmitted) {
            that._emit('STREAMING_OFFER_RECEIVED', {});
          }
          if (that._pendingWatch && (jsep || (isWatchResult && !is460))) {
            that._pendingWatch.resolve(true);
            that._pendingWatch = null;
          }
          that._emit('MESSAGE', { msg });
        },
        webrtcState: (up, reason) => {
          if (that._dropIfStaleGen(gen, 'handle_webrtcState')) return;
          that._emit('WEBRTC_STATE', { up: !!up, reason: String(reason || '') });
        },
        iceState: (state) => {
          if (that._dropIfStaleGen(gen, 'handle_iceState')) return;
          that._emit('ICE_STATE', { state: String(state) });
        },
        onremotetrack: (track, mid, on) => {
          if (that._dropIfStaleGen(gen, 'handle_onremotetrack')) return;
          that._onRemoteTrack(gen, track, mid, on);
        },
        oncleanup: () => {
          if (that._dropIfStaleGen(gen, 'handle_oncleanup')) return;
          if (that._cleanupResolve) {
            that._cleanupResolve();
            that._cleanupResolve = null;
          }
          that._emit('CLEANUP', {});
        },
        ondetached: () => {
          if (that._dropIfStaleGen(gen, 'handle_ondetached')) return;
          that._emit('HANDLE_DETACHED', {});
        },
        slowLink: (uplink, lost, mid) => {
          if (that._dropIfStaleGen(gen, 'handle_slowLink')) return;
          that._emit('SLOW_LINK', { uplink: !!uplink, lost, mid });
        },
        hangup: (reason) => {
          if (that._dropIfStaleGen(gen, 'handle_hangup')) return;
          that._emit('HANGUP', { reason: String(reason || '') });
        },
      });

      // Guard stale attachments
      if (this._dropIfStaleGen(gen, 'attach_complete')) {
        try { handle.detach && handle.detach(); } catch(_) {}
        return;
      }
      this.handle = handle;
      this._emit('HANDLE_ATTACHED', {});
    }

    _onJsepOffer(gen, jsep){
      const h = this.handle;
      if (!h) return;

      // DEF-07: Validate SDP to prevent oversized/malicious offers
      const MAX_SDP_BYTES = 16384;
      if (!jsep?.sdp || typeof jsep.sdp !== 'string') {
        this._emit('ERROR', { where: '_onJsepOffer', error: 'missing or invalid SDP' });
        return;
      }
      if (jsep.sdp.length > MAX_SDP_BYTES) {
        this._emit('ERROR', { where: '_onJsepOffer', error: `SDP too large: ${jsep.sdp.length}b (max ${MAX_SDP_BYTES})` });
        return;
      }

      // DEF-02: Verify SDP contains DTLS fingerprint (defense-in-depth)
      if (!/a=fingerprint:/m.test(jsep.sdp)) {
        this.log.warn('sdp_missing_dtls_fingerprint', { sdp_length: jsep.sdp.length });
        this._emit('ERROR', { where: '_onJsepOffer', error: 'SDP missing DTLS fingerprint' });
        return;
      }

      // NEW-03: Emit STREAMING_OFFER_RECEIVED only after SDP passes validation
      this._emit('STREAMING_OFFER_RECEIVED', {});

      try {
        const wantRestart = this._wantIceRestart;
      this._wantIceRestart = false;
      h.createAnswer({
          jsep,
          iceRestart: wantRestart,
          tracks: [
            { type: 'video', recv: true, add: false },
            { type: 'audio', recv: false, add: false },
          ],
          success: (ourJsep) => {
            if (this._dropIfStaleGen(gen, 'createAnswer_success')) return;
            h.send({
              message: { request: 'start' },
              jsep: ourJsep,
              error: (err) => {
                if (this._dropIfStaleGen(gen, 'send_start_error')) return;
                this._emit('ERROR', { where: 'send_start', error: err });
              },
            });
          },
          error: (err) => {
            if (this._dropIfStaleGen(gen, 'createAnswer_error')) return;
            const code = err && (err.error_code ?? err.errorCode);
            this._emit('ERROR', { where: 'createAnswer', error: err, error_code: code });
          },
        });
      } catch (e) {
        if (this._dropIfStaleGen(gen, 'createAnswer_throw')) return;
        this._emit('ERROR', { where: 'createAnswer_throw', error: e });
      }
    }

    _onRemoteTrack(gen, track, mid, on){
      const midKey = String(mid || '');
      if (!on) {
        const prev = this._tracksByMid.get(midKey);
        if (prev) {
          try { this._inboundStream.removeTrack(prev); } catch(_) {}
          this._tracksByMid.delete(midKey);
        }
        this._emit('TRACK', { track, on: false, mid: midKey });
        return;
      }

      if (track && track.kind === 'video') {
        const prev = this._tracksByMid.get(midKey);
        if (prev && prev !== track) {
          try { this._inboundStream.removeTrack(prev); } catch(_) {}
        }
        this._tracksByMid.set(midKey, track);
        try { this._inboundStream.addTrack(track); } catch(_) {}

        const id = track.id || midKey;
        track.onmute = () => { if (gen === this._handleGen) this._emit('TRACK_MUTED', { trackId: id, mid: midKey }); };
        track.onunmute = () => { if (gen === this._handleGen) this._emit('TRACK_UNMUTED', { trackId: id, mid: midKey }); };
        track.onended = () => { if (gen === this._handleGen) this._emit('TRACK_ENDED', { trackId: id, mid: midKey, kind: 'video' }); };

        this._emit('TRACK', { track, on: true, mid: midKey });
      }
    }

    async _detachHandle(){
      const h = this.handle;
      if (!h) return;
      const oldGen = this._handleGen;
      this._handleGen += 1;
      this.handle = null;
      if (this._cleanupResolve) {
        this._cleanupResolve();
        this._cleanupResolve = null;
      }
      this._clearAllTracks();

      await new Promise((resolve) => {
        try {
          h.detach({ success: () => resolve(true), error: () => resolve(true) });
        } catch (_) {
          resolve(true);
        }
      });

      this._emit('HANDLE_DETACHED', { oldGen });
    }
  }

  AP.Adapters.JanusStreamingAdapter = JanusStreamingAdapter;
})();
