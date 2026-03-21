(function(){
  'use strict';
  const AP = window.AutonomousPlayer;
  if (!AP) throw new Error('AutonomousPlayer namespace missing');

  const PlayerState = AP.Core.PlayerState;
  const statusTextFor = AP.Core.statusTextFor;

  /** @implements {AP.Ports.VideoPort} */
  class DomUIAdapter {
    /**
     * @param {any} cfg
     * @param {any} logger
     * @param {any} clock
     */
    constructor(cfg, logger, clock){
      this.cfg = cfg;
      this.log = logger;
      this.clock = clock;

      this._video = document.getElementById(cfg.videoId);
      this.playBtn = document.getElementById(cfg.playButtonId);
      this.statsBtn = document.getElementById(cfg.statsButtonId);
      this.statsBox = document.getElementById(cfg.statsBoxId);
      this.statusPill = document.getElementById(cfg.statusPillId);
      this.debugPanel = document.getElementById(cfg.debugPanelId);

      if (!this._video) throw new Error(`Missing video element #${cfg.videoId}`);
      if (!this.playBtn) throw new Error(`Missing button #${cfg.playButtonId}`);
      if (!this.statsBtn) throw new Error(`Missing button #${cfg.statsButtonId}`);
      if (!this.statsBox) throw new Error(`Missing stats box #${cfg.statsBoxId}`);
      if (!this.statusPill) throw new Error(`Missing status pill #${cfg.statusPillId}`);
      if (!this.debugPanel) throw new Error(`Missing debug panel #${cfg.debugPanelId}`);

      this._statsVisible = false;
      this._cleanMode = !new URLSearchParams(window.location.search).has('controls');
      this._intentHandlers = { onTogglePlay: null, onRetry: null, onToggleStats: null };

      // Frame clock (single installation)
      this._frameClockInstalled = false;
      this._frameTick = null;
      this._frameIntervalId = null;

      // Video stall detection callback
      this._videoStalledCb = null;
      this._stalledDebounceTimer = null;

      // Ensure autoplay-friendly attributes
      this._video.autoplay = true;
      this._video.playsInline = true;
      this._video.muted = true;

      this._wireDom();

      // Element-level visibility (IntersectionObserver) — detects iframe/CSS hiding
      this._elementVisible = true;
      this._elementVisibilityCb = null;
      this._intersectionObserver = null;
      if (typeof IntersectionObserver !== 'undefined') {
        this._intersectionObserver = new IntersectionObserver((entries) => {
          const wasVisible = this._elementVisible;
          this._elementVisible = entries[0].isIntersecting;
          if (wasVisible !== this._elementVisible && this._elementVisibilityCb) {
            try { this._elementVisibilityCb(this._elementVisible); } catch(_) {}
          }
        }, { threshold: 0 });
        this._intersectionObserver.observe(this._video);
      }
    }

    _wireDom(){
      this._boundPlayClick = () => {
        const h = this._intentHandlers;
        if (this.playBtn.dataset.mode === 'retry') {
          h.onRetry && h.onRetry();
        } else {
          h.onTogglePlay && h.onTogglePlay();
        }
      };
      this.playBtn.addEventListener('click', this._boundPlayClick);

      this._boundStatsClick = () => {
        this._statsVisible = !this._statsVisible;
        this.statsBox.style.display = this._statsVisible ? 'block' : 'none';
        this.statsBtn.textContent = this._statsVisible ? 'Hide stats' : 'Show stats';
        const h = this._intentHandlers;
        h.onToggleStats && h.onToggleStats(this._statsVisible);
      };
      this.statsBtn.addEventListener('click', this._boundStatsClick);

      // Video stall detection: browser fires 'stalled' when fetching media data has stalled
      // and 'waiting' when playback stopped because of temporary buffer underrun.
      this._boundStalled = () => this._onVideoStallEvent('stalled');
      this._boundWaiting = () => this._onVideoStallEvent('waiting');
      this._video.addEventListener('stalled', this._boundStalled);
      this._video.addEventListener('waiting', this._boundWaiting);

      // start hidden
      this.statsBox.style.display = 'none';
      this.statusPill.style.display = 'none';
      this.debugPanel.style.display = 'none';
      if (this._cleanMode) {
        this.playBtn.style.display = 'none';
        this.statsBtn.style.display = 'none';
      }
    }

    /** Debounced video stall handler — coalesces rapid stalled+waiting bursts. */
    _onVideoStallEvent(eventName){
      if (!this._videoStalledCb) return;
      if (this._stalledDebounceTimer) return; // already debounced
      this._stalledDebounceTimer = this.clock.setTimeout(() => {
        this._stalledDebounceTimer = null;
        try { this._videoStalledCb(eventName); } catch(_) {}
      }, 500);
    }

    /**
     * Register callback for video stall events (stalled/waiting).
     * @param {function(string): void} cb called with event name
     */
    onVideoStalled(cb){
      this._videoStalledCb = typeof cb === 'function' ? cb : null;
    }

    /** @returns {boolean} true if the video element is intersecting the viewport */
    isElementVisible(){ return this._elementVisible; }

    /** Register callback for element visibility changes (IntersectionObserver). */
    onElementVisibilityChange(cb){
      this._elementVisibilityCb = typeof cb === 'function' ? cb : null;
    }

    bindIntents(handlers){
      this._intentHandlers = Object.assign(this._intentHandlers, handlers || {});
    }

    setStatsText(text){
      this.statsBox.textContent = text || '';
    }

    setDebugText(text){
      if (!this.cfg.debugPanelEnabled || this._cleanMode) return;
      this.debugPanel.textContent = text || '';
      this.debugPanel.style.display = 'block';
    }

    hideDebug(){
      this.debugPanel.style.display = 'none';
    }

    /**
     * Calls `onFrame()` each time a new frame is observed.
     * Installed once; safe against reconnect loops.
     * @param {() => void} onFrame
     */
    startFrameClock(onFrame){
      if (this._frameClockInstalled) return;
      this._frameClockInstalled = true;
      this._frameTick = (typeof onFrame === 'function') ? onFrame : () => {};

      const v = this._video;

      // Prefer requestVideoFrameCallback if available.
      if (typeof v.requestVideoFrameCallback === 'function') {
        const self = this;
        const loop = () => {
          if (!self._frameClockInstalled) return; // stop rescheduling after stopFrameClock
          try { self._frameTick && self._frameTick(); } catch(_) {}
          try { v.requestVideoFrameCallback(loop); } catch(_) {}
        };
        try { v.requestVideoFrameCallback(loop); } catch (e) {
          this.log.warn('rvfc_install_failed', { error: String(e?.message || e) });
        }
        return;
      }

      // Fallback: detect progress in currentTime.
      let lastTime = v.currentTime || 0;
      this._frameIntervalId = this.clock.setInterval(() => {
        if (v.readyState >= 2 && v.currentTime !== lastTime) {
          lastTime = v.currentTime;
          try { this._frameTick && this._frameTick(); } catch(_) {}
        }
      }, 250);
    }

    stopFrameClock(){
      if (this._frameIntervalId) {
        this.clock.clearInterval(this._frameIntervalId);
        this._frameIntervalId = null;
      }
      this._frameTick = null;
      this._frameClockInstalled = false;
    }

    /**
     * @param {{state:string, attempt:number, desiredPlaying:boolean, errCode?:string, autoplayBlocked?:boolean, debugText?:string}} vm
     */
    render(vm){
      if (this._cleanMode) {
        this.statusPill.dataset.state = vm.degraded ? 'DEGRADED' : vm.state;
        return;
      }

      const st = vm.state;
      const attempt = vm.attempt || 0;
      const errCode = vm.errCode;

      this.statusPill.textContent = statusTextFor(st, attempt, errCode);
      this.statusPill.dataset.state = vm.degraded ? 'DEGRADED' : st;
      this.statusPill.style.display = 'flex';

      if (this.cfg.debugPanelEnabled) {
        this.setDebugText(vm.debugText || '');
      }

      // Button policy
      if (this.cfg.autoplayEnabled) {
        // Hide play in normal operation; show Retry only on ERROR/autoplayBlocked.
        if (st === PlayerState.ERROR || vm.autoplayBlocked) {
          this.playBtn.style.display = 'block';
          this.playBtn.textContent = 'Retry';
          this.playBtn.dataset.mode = 'retry';
          this.playBtn.setAttribute('aria-label', 'Retry connection');
        } else {
          this.playBtn.style.display = 'none';
          this.playBtn.dataset.mode = '';
        }
      } else {
        this.playBtn.style.display = 'block';
        if (st === PlayerState.ERROR) {
          this.playBtn.textContent = 'Retry';
          this.playBtn.dataset.mode = 'retry';
          this.playBtn.setAttribute('aria-label', 'Retry connection');
        } else {
          const label = vm.desiredPlaying ? 'Stop' : 'Play';
          this.playBtn.textContent = label;
          this.playBtn.dataset.mode = '';
          this.playBtn.setAttribute('aria-label', label);
        }
      }
    }

    // --------
    // VideoPort-ish
    // --------

    bindStream(stream){
      try {
        const prev = this._video.srcObject;
        // Full reset: if the stream changed, clear srcObject first to flush
        // the browser's decoder state and prevent stale "ghost" frames.
        if (prev && prev !== stream) {
          this._video.srcObject = null;
        }
        this._video.srcObject = stream;
      } catch (e) {
        this.log.warn('video_bind_failed', { error: String(e) });
      }
    }

    async ensurePlaying(){
      try {
        await this._video.play();
        return { ok: true, blocked: false };
      } catch (e) {
        const name = e && e.name ? String(e.name) : '';
        if (name === 'NotAllowedError') return { ok: false, blocked: true, error: e };
        return { ok: false, blocked: false, error: e };
      }
    }

    onVideoEvent(type, handler){
      this._video.addEventListener(type, handler);
    }

    destroy(){
      this.playBtn.removeEventListener('click', this._boundPlayClick);
      this.statsBtn.removeEventListener('click', this._boundStatsClick);
      this._video.removeEventListener('stalled', this._boundStalled);
      this._video.removeEventListener('waiting', this._boundWaiting);
      if (this._stalledDebounceTimer) {
        this.clock.clearTimeout(this._stalledDebounceTimer);
        this._stalledDebounceTimer = null;
      }
      this.stopFrameClock();
      if (this._intersectionObserver) {
        this._intersectionObserver.disconnect();
        this._intersectionObserver = null;
      }
    }
  }

  AP.Adapters.DomUIAdapter = DomUIAdapter;
})();
