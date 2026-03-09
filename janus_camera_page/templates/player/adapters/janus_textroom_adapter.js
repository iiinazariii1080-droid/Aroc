(function(){
  'use strict';
  const AP = window.AutonomousPlayer;
  if (!AP) throw new Error('AutonomousPlayer namespace missing');

  const TEXTROOM_ROOM_ID = 1000;

  class JanusTextRoomAdapter {
    /**
     * @param {any} cfg
     * @param {any} logger
     * @param {any} sessionManager
     */
    constructor(cfg, logger, sessionManager){
      this.cfg = cfg;
      this.log = logger;
      this.session = sessionManager;
      if (!this.session) throw new Error('JanusSessionManager required');

      this.handle = null;
      this.ready = false;
      this.username = `viewer-${Math.random().toString(16).slice(2, 8)}`;

      // Invalidate on session reset
      this._unsubSession = this.session.onEvent((ev) => {
        if (!ev || !ev.type) return;
        if (ev.type === 'SESSION_DESTROYED' || ev.type === 'SESSION_RECREATED') {
          this.handle = null;
          this.ready = false;
        }
      });
    }

    async attach(){
      await this.session.init(); // rtcConfig should have been set in bootstrap
      const that = this;

      const handle = await this.session.attach('janus.plugin.textroom', {
        opaqueId: `textroom-${Janus.randomString(8)}`,
        onmessage: (_msg, jsep) => {
          if (!jsep) return;
          that._createAnswer(jsep);
        },
        ondataopen: () => {
          that.ready = true;
          that._join();
        },
        ondata: (data) => {
          if (that.cfg.debug) that.log.debug('textroom_data', { data: String(data).slice(0, 200) });
        },
        oncleanup: () => {
          that.handle = null;
          that.ready = false;
        },
      });

      this.handle = handle;
      try { handle.send({ message: { request: 'setup' } }); } catch(e) { console.warn('[textroom] setup send failed:', e); }
    }

    _createAnswer(jsep){
      const h = this.handle;
      if (!h) return;
      const that = this;
      h.createAnswer({
        jsep,
        tracks: [ { type: 'data' } ],
        trickle: true,
        success: (jsepAnswer) => {
          try { h.send({ message: { request: 'ack' }, jsep: jsepAnswer }); } catch(e) { console.warn('[textroom] ack send failed:', e); }
        },
        error: (err) => that.log.warn('textroom_answer_error', { error: String(err?.message || err) }),
      });
    }

    _join(){
      if (!this.handle || !this.ready) return;
      const payload = {
        textroom: 'join',
        room: TEXTROOM_ROOM_ID,
        username: this.username,
        display: this.username,
        transaction: Janus.randomString(12),
      };
      this.handle.data({
        text: JSON.stringify(payload),
        error: (err) => this.log.warn('textroom_join_error', { error: String(err?.message || err) }),
      });
    }

    sendFrame(frame){
      if (!this.handle || !this.ready) return;
      const envelope = {
        textroom: 'message',
        room: TEXTROOM_ROOM_ID,
        text: JSON.stringify(frame),
        transaction: Janus.randomString(12),
      };
      this.handle.data({
        text: JSON.stringify(envelope),
        error: (err) => this.log.warn('textroom_send_error', { error: String(err?.message || err) }),
      });
    }

    /**
     * Send a ping frame through the same DataChannel path as joystick frames.
     * Used for e2e latency measurement.
     * @param {number} id  Monotonic ping id
     */
    sendPing(id){
      if (!this.handle || !this.ready) return;
      const pingFrame = { type: 'ping', id: id, ts: Date.now() };
      const envelope = {
        textroom: 'message',
        room: TEXTROOM_ROOM_ID,
        text: JSON.stringify(pingFrame),
        transaction: Janus.randomString(12),
      };
      this.handle.data({
        text: JSON.stringify(envelope),
        error: (err) => this.log.warn('textroom_ping_error', { error: String(err?.message || err) }),
      });
    }

    async detach(){
      // Unsubscribe from session events to prevent listener accumulation.
      if (this._unsubSession) {
        this._unsubSession();
        this._unsubSession = null;
      }
      const h = this.handle;
      this.handle = null;
      this.ready = false;
      if (!h) return;
      await new Promise((resolve) => {
        try { h.detach({ success: () => resolve(true), error: () => resolve(true) }); } catch(e) { console.warn('[textroom] detach error:', e); resolve(true); }
      });
    }
  }

  AP.Adapters.JanusTextRoomAdapter = JanusTextRoomAdapter;
})();
