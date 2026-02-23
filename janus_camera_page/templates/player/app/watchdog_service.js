(function(){
  'use strict';
  const AP = window.AutonomousPlayer;
  if (!AP) throw new Error('AutonomousPlayer namespace missing');

  /**
   * Watchdog: ticks on an interval and calls onTimeout when last frame age exceeds threshold.
   * Does not interpret state; the controller decides whether to act in the callback.
   *
   * @param {{ noFrameThresholdMs: number, watchdogTickMs: number }} cfg
   * @param {*} clock port with setInterval/clearInterval/nowMs
   * @param {function(number): void} onTimeout called with ageMs when age > noFrameThresholdMs
   */
  class WatchdogService {
    constructor(cfg, clock, onTimeout){
      this.cfg = cfg;
      this.clock = clock;
      this.onTimeout = onTimeout;
      this._lastFrameAt = 0;
      this._timer = null;
    }

    /**
     * Call on each video frame to refresh the timestamp.
     */
    updateFrameTime(){
      this._lastFrameAt = this.clock.nowMs();
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
      if (this._timer != null) return;
      this._lastFrameAt = this._lastFrameAt || this.clock.nowMs();
      this._timer = this.clock.setInterval(() => {
        const now = this.clock.nowMs();
        const age = this.getLastFrameAgeMs(now);
        if (age > this.cfg.noFrameThresholdMs) {
          this.onTimeout(age);
        }
      }, this.cfg.watchdogTickMs);
    }

    stop(){
      if (this._timer != null) {
        this.clock.clearInterval(this._timer);
        this._timer = null;
      }
    }
  }

  AP.App.WatchdogService = WatchdogService;
})();
