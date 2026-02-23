(function(){
  'use strict';
  const AP = window.AutonomousPlayer;
  if (!AP) throw new Error('AutonomousPlayer namespace missing');

  /**
   * JanusSessionManager is an infrastructure adapter that owns the Janus session lifecycle.
   * Other Janus adapters (Streaming/TextRoom) attach plugin handles through this manager.
   *
   * App/Core MUST NOT access the raw Janus session object. Only adapters use it.
   */
  class JanusSessionManager {
    /**
     * @param {any} cfg
     * @param {any} logger
     */
    constructor(cfg, logger){
      this.cfg = cfg;
      this.log = logger;

      this.janus = null;
      this._rtcConfig = null;

      this._ensurePromise = null;
      this._gen = 0;
      this._observers = new Set();
      this._destroyingByUs = false;
    }

    /**
     * Store RTC config for future init. Idempotent.
     * @param {{iceServers:any[], iceTransportPolicy:string}} rtcConfig
     */
    setRtcConfig(rtcConfig){
      if (!rtcConfig) return;
      this._rtcConfig = rtcConfig;
    }

    generation(){
      return this._gen;
    }

    onEvent(cb){
      if (typeof cb !== 'function') return () => {};
      this._observers.add(cb);
      return () => this._observers.delete(cb);
    }

    _emit(type, payload){
      for (const cb of Array.from(this._observers)) {
        try { cb({ type, payload }); } catch (_) {}
      }
    }

    async init(rtcConfig){
      if (rtcConfig) this.setRtcConfig(rtcConfig);
      if (this.janus) return;

      if (this._ensurePromise) return this._ensurePromise;

      const that = this;
      this._ensurePromise = (async () => {
        const cfg = that.cfg;
        const servers = [cfg.janusWs, cfg.janusRest].filter(Boolean);
        const server = servers.length === 1 ? servers[0] : servers;
        if (!servers.length) throw new Error('Missing Janus server endpoint');

        if (!that._rtcConfig) {
          throw new Error('RTC config not set; call sessionManager.init(rtcConfig) during bootstrap');
        }

        const iceServers = that._rtcConfig.iceServers;
        const iceTransportPolicy = that._rtcConfig.iceTransportPolicy || 'all';

        await new Promise((resolve, reject) => {
          try {
            that.janus = new Janus({
              server,
              iceServers,
              iceTransportPolicy,
              ipv6: false,
              withCredentials: false,
              destroyOnUnload: true,
              success: () => {
                that._emit('SESSION_READY', { gen: that._gen });
                resolve(true);
              },
              error: (err) => {
                that._emit('SESSION_ERROR', { where: 'create_session', error: err });
                reject(err);
              },
              destroyed: () => {
                // Only emit when session died externally; we already emit in destroy() when we call j.destroy()
                if (!that._destroyingByUs) that._emit('SESSION_DESTROYED', { gen: that._gen });
              },
            });
          } catch (e) {
            that._emit('SESSION_ERROR', { where: 'create_session_throw', error: e });
            reject(e);
          }
        });
      })().finally(() => {
        that._ensurePromise = null;
      });

      return this._ensurePromise;
    }

    /**
     * Attach a plugin handle to the active session.
     * @param {string} plugin
     * @param {object} callbacks - Janus attach callbacks excluding `plugin`.
     * @returns {Promise<any>} handle
     */
    async attach(plugin, callbacks){
      await this.init();
      const janus = this.janus;
      if (!janus) throw new Error('Janus session not available');

      return await new Promise((resolve, reject) => {
        try {
          janus.attach(Object.assign({}, callbacks || {}, {
            plugin,
            success: (handle) => resolve(handle),
            error: (err) => reject(err),
          }));
        } catch (e) {
          reject(e);
        }
      });
    }

    async destroy(){
      const j = this.janus;
      this.janus = null;
      this._gen += 1;

      if (!j) {
        this._emit('SESSION_DESTROYED', { gen: this._gen });
        return;
      }

      this._destroyingByUs = true;
      try {
        await new Promise((resolve) => {
          try {
            j.destroy({ success: () => resolve(true), error: () => resolve(true) });
          } catch (_) {
            resolve(true);
          }
        });
        this._emit('SESSION_DESTROYED', { gen: this._gen });
      } finally {
        this._destroyingByUs = false;
      }
    }

    async recreate(rtcConfig){
      // increments generation inside destroy()
      await this.destroy();
      if (rtcConfig) this.setRtcConfig(rtcConfig);
      await this.init();
      this._emit('SESSION_RECREATED', { gen: this._gen });
    }
  }

  AP.Adapters.JanusSessionManager = JanusSessionManager;
})();
