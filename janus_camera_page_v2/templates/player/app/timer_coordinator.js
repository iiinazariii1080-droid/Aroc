(function(){
  'use strict';
  const AP = window.AutonomousPlayer;
  if (!AP) throw new Error('AutonomousPlayer namespace missing');

  /**
   * Centralized timeout management. Used for ICE grace, track mute, etc.
   * @param {*} clock port with setTimeout/clearTimeout
   */
  class TimerCoordinator {
    constructor(clock){
      this.clock = clock;
      this._timers = new Map();
    }

    /**
     * Set or replace timeout by key. Replaces any existing timeout for the same key.
     * @param {string} key
     * @param {function} callback
     * @param {number} ms
     */
    set(key, callback, ms){
      this.clear(key);
      const id = this.clock.setTimeout(() => {
        this._timers.delete(key);
        callback();
      }, ms);
      this._timers.set(key, id);
    }

    /**
     * Clear timeout for key.
     * @param {string} key
     */
    clear(key){
      const id = this._timers.get(key);
      if (id != null) {
        this.clock.clearTimeout(id);
        this._timers.delete(key);
      }
    }

    /**
     * Clear all timeouts whose key starts with prefix (e.g. 'trackMute:').
     * @param {string} prefix
     */
    clearPrefix(prefix){
      for (const k of this._timers.keys()) {
        if (k.startsWith(prefix)) this.clear(k);
      }
    }

    /**
     * Clear all timeouts.
     */
    clearAll(){
      for (const id of this._timers.values()) {
        this.clock.clearTimeout(id);
      }
      this._timers.clear();
    }

    /**
     * Whether a timeout is currently set for key.
     * @param {string} key
     * @returns {boolean}
     */
    has(key){
      return this._timers.has(key);
    }
  }

  AP.App.TimerCoordinator = TimerCoordinator;
})();
