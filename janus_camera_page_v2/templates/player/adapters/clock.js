(function(){
  'use strict';
  const AP = window.AutonomousPlayer;
  if (!AP) throw new Error('AutonomousPlayer namespace missing');

  /**
   * @returns {AP.Ports.ClockPort}
   */
  function createClock(){
    // Leak guard / debug counters (cheap, helpful in reconnect loops).
    const timeouts = new Map();
    const intervals = new Map();

    function nowMs(){ return Date.now(); }

    function setTimeoutFn(fn, ms){
      let id = null;
      const wrapped = () => {
        // Ensure we always drop accounting, even if fn throws.
        if (id != null) timeouts.delete(id);
        try { fn(); } catch (e) { /* swallow to avoid breaking timer loop */ }
      };
      id = window.setTimeout(wrapped, ms);
      timeouts.set(id, { ms: Math.max(0, Math.trunc(ms || 0)), createdMs: nowMs() });
      return id;
    }

    function clearTimeoutFn(id){
      if (id != null) timeouts.delete(id);
      return window.clearTimeout(id);
    }

    function setIntervalFn(fn, ms){
      const id = window.setInterval(fn, ms);
      intervals.set(id, { ms: Math.max(1, Math.trunc(ms || 1)), createdMs: nowMs() });
      return id;
    }

    function clearIntervalFn(id){
      if (id != null) intervals.delete(id);
      return window.clearInterval(id);
    }

    function debugSnapshot(){
      return { timeouts: timeouts.size, intervals: intervals.size };
    }

    return {
      nowMs,
      setTimeout: setTimeoutFn,
      clearTimeout: clearTimeoutFn,
      setInterval: setIntervalFn,
      clearInterval: clearIntervalFn,
      debugSnapshot,
    };
  }

  AP.Adapters.createClock = createClock;
})();
