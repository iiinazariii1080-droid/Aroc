(function(){
  'use strict';
  const AP = window.AutonomousPlayer;
  if (!AP) throw new Error('AutonomousPlayer namespace missing');

  /** Number of frame timestamps kept in the sliding window for FPS calculation. */
  const FPS_RING_SIZE = 60;

  /**
   * Watchdog: ticks on an interval and calls onTimeout when last frame age exceeds threshold.
   * Additionally tracks sliding-window FPS and calls onFpsDrop when FPS stays below
   * minAcceptableFps for longer than fpsDropThresholdMs.
   *
   * Does not interpret state; the controller decides whether to act in the callbacks.
   *
   * @param {{ noFrameThresholdMs: number, watchdogTickMs: number,
   *           minAcceptableFps?: number, fpsDropThresholdMs?: number }} cfg
   * @param {*} clock port with setInterval/clearInterval/nowMs
   * @param {function(number): void} onTimeout called with ageMs when age > noFrameThresholdMs
   * @param {function(number): void} [onFpsDrop] called with current fps when sustained low-fps detected
   */
  class WatchdogService {
    constructor(cfg, clock, onTimeout, onFpsDrop){
      this.cfg = cfg;
      this.clock = clock;
      this.onTimeout = onTimeout;
      this.onFpsDrop = typeof onFpsDrop === 'function' ? onFpsDrop : null;
      this._lastFrameAt = 0;
      this._timer = null;

      // --- FPS sliding window ---
      this._fpsRing = [];           // circular buffer of frame timestamps (ms)
      this._fpsRingIdx = 0;         // write cursor
      this._fpsRingFull = false;    // true once ring wraps around
      this._fpsDropSince = 0;       // timestamp when FPS first dropped below threshold (0 = not dropping)
      this._fpsDropFired = false;   // latch: only fire onFpsDrop once per degraded period
    }

    /**
     * Call on each video frame to refresh the timestamp and record in FPS ring.
     */
    updateFrameTime(){
      const now = this.clock.nowMs();
      this._lastFrameAt = now;

      // Record in ring buffer
      this._fpsRing[this._fpsRingIdx] = now;
      this._fpsRingIdx = (this._fpsRingIdx + 1) % FPS_RING_SIZE;
      if (!this._fpsRingFull && this._fpsRingIdx === 0) this._fpsRingFull = true;
    }

    /**
     * Compute current FPS from the sliding window.
     * @param {number} [now]
     * @returns {number} estimated FPS (0 if no data)
     */
    getCurrentFps(now){
      now = now || this.clock.nowMs();
      const count = this._fpsRingFull ? FPS_RING_SIZE : this._fpsRingIdx;
      if (count < 2) return 0;

      // Find oldest timestamp in the ring
      const oldestIdx = this._fpsRingFull ? this._fpsRingIdx : 0;
      const oldest = this._fpsRing[oldestIdx];
      const spanMs = now - oldest;
      if (spanMs <= 0) return 0;

      // FPS = (intervals / span) * 1000; intervals = count - 1
      return ((count - 1) / spanMs) * 1000;
    }

    /**
     * Age in ms since last frame (for snapshot / health checks).
     * @param {number} now
     * @returns {number}
     */
    getLastFrameAgeMs(now){
      return Math.max(0, (now || this.clock.nowMs()) - this._lastFrameAt);
    }

    start(){
      if (this._timer != null) {
        this.clock.clearInterval(this._timer);
        this._timer = null;
      }
      this._lastFrameAt = this._lastFrameAt || this.clock.nowMs();
      this._timer = this.clock.setInterval(() => {
        const now = this.clock.nowMs();
        const age = this.getLastFrameAgeMs(now);

        // Classic no-frame watchdog
        if (age > this.cfg.noFrameThresholdMs) {
          try { this.onTimeout(age); } catch (e) { console.error('[watchdog] onTimeout threw:', e); }
          return; // no-frame already triggers recovery; skip FPS check
        }

        // FPS-drop watchdog
        if (this.onFpsDrop && this.cfg.minAcceptableFps > 0) {
          const fps = this.getCurrentFps(now);
          const thresholdMs = this.cfg.fpsDropThresholdMs || 3000;

          if (fps > 0 && fps < this.cfg.minAcceptableFps) {
            if (this._fpsDropSince === 0) {
              this._fpsDropSince = now;
            } else if (!this._fpsDropFired && (now - this._fpsDropSince) >= thresholdMs) {
              this._fpsDropFired = true;
              try { this.onFpsDrop(fps); } catch (e) { console.error('[watchdog] onFpsDrop threw:', e); }
            }
          } else {
            // FPS recovered — reset latch
            this._fpsDropSince = 0;
            this._fpsDropFired = false;
          }
        }
      }, this.cfg.watchdogTickMs);
    }

    stop(){
      if (this._timer != null) {
        this.clock.clearInterval(this._timer);
        this._timer = null;
      }
      this._fpsDropSince = 0;
      this._fpsDropFired = false;
    }

    /** Reset FPS ring and drop latch (call on successful reconnect). */
    resetFpsTracking(){
      this._fpsRing = [];
      this._fpsRingIdx = 0;
      this._fpsRingFull = false;
      this._fpsDropSince = 0;
      this._fpsDropFired = false;
    }

    /**
     * Reset watchdog state after a tab resume. Sets _lastFrameAt to "now" so the
     * stream has a full noFrameThresholdMs window to deliver the next frame before
     * the watchdog fires. Also resets FPS tracking to avoid false FPS-drop detections
     * from stale ring buffer entries accumulated before the tab-hide.
     */
    resetAfterTabResume(){
      this._lastFrameAt = this.clock.nowMs();
      this.resetFpsTracking();
    }

    /**
     * Reset the no-frame origin to "now", giving the stream a fresh
     * noFrameThresholdMs window.  Call when the media path becomes ready
     * (e.g. ICE reaches 'connected') so that TURN relay latency during
     * ICE negotiation does not count against the no-frame budget.
     */
    resetOrigin(){
      this._lastFrameAt = this.clock.nowMs();
    }
  }

  AP.App.WatchdogService = WatchdogService;
})();
