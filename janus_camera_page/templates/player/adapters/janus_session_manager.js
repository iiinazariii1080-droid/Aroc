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
      const initGen = this._gen;
      const INIT_TIMEOUT_MS = 8000;

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

        let initTimeoutId = null;

        const janusPromise = new Promise((resolve, reject) => {
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

        const timeoutPromise = new Promise((_, reject) => {
          initTimeoutId = setTimeout(() => {
            // Only clean up if no destroy() was called since this init started.
            if (that._gen === initGen) {
              const zombie = that.janus;
              that.janus = null;
              if (zombie) {
                try { zombie.destroy({ asyncRequest: true }); } catch (_) {}
              }
            }
            reject(new Error('janus_init_timeout'));
          }, INIT_TIMEOUT_MS);
        });

        try {
          await Promise.race([janusPromise, timeoutPromise]);
        } finally {
          if (initTimeoutId != null) clearTimeout(initTimeoutId);
        }
      })().finally(() => {
        // Only clear if no destroy()+init() happened since this init started.
        if (that._gen === initGen) {
          that._ensurePromise = null;
        }
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

    /**
     * Returns true if the Janus session exists and its underlying WebSocket is connected.
     * Used to short-circuit SOFT_RESTART / REATTACH attempts when the server is unreachable.
     */
    isAlive(){
      return !!(this.janus && typeof this.janus.isConnected === 'function' && this.janus.isConnected());
    }

    async destroy(){
      const j = this.janus;
      this.janus = null;
      this._gen += 1;
      const destroyGen = this._gen;
      this._ensurePromise = null;

      if (!j) {
        this._emit('SESSION_DESTROYED', { gen: destroyGen });
        return;
      }

      this._destroyingByUs = true;
      try {
        const DESTROY_TIMEOUT_MS = 5000;
        const destroyPromise = new Promise((resolve) => {
          try {
            j.destroy({ success: () => resolve(true), error: () => resolve(true) });
          } catch (_) {
            resolve(true);
          }
        });
        const timeoutPromise = new Promise((resolve) => {
          setTimeout(() => resolve(true), DESTROY_TIMEOUT_MS);
        });
        await Promise.race([destroyPromise, timeoutPromise]);
        // Only emit if no newer destroy happened while we were waiting.
        if (this._gen === destroyGen) {
          this._emit('SESSION_DESTROYED', { gen: destroyGen });
        }
      } finally {
        this._destroyingByUs = false;
      }
    }

    async recreate(rtcConfig){
      const beforeGen = this._gen;
      // increments generation inside destroy()
      await this.destroy();
      const expectedGen = beforeGen + 1;
      if (rtcConfig) this.setRtcConfig(rtcConfig);
      await this.init();
      // Only emit if no concurrent recreate happened.
      if (this._gen === expectedGen) {
        this._emit('SESSION_RECREATED', { gen: this._gen });
      }
    }
  }

  AP.Adapters.JanusSessionManager = JanusSessionManager;
})();
