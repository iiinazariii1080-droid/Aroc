(function(){
  'use strict';
  const AP = window.AutonomousPlayer;
  if (!AP) throw new Error('AutonomousPlayer namespace missing');

  /**
   * @param {{ debug?: boolean, prefix?: string, run_id?: string }} opts
   * @returns {AP.Ports.LoggerPort}
   */
  function createConsoleLogger(opts){
    const enabledDebug = !!(opts && opts.debug);
    const prefix = (opts && opts.prefix) || '[AutonomousPlayer]';
    const runId = opts && opts.run_id;

    function withRunId(payload){
      if (!runId) return payload;
      const p = (payload != null && typeof payload === 'object' && !Array.isArray(payload)) ? payload : {};
      return Object.assign({ run_id: runId }, p);
    }

    function fmt(type, payload){
      if (payload === undefined) return `${prefix} ${type}`;
      try {
        return `${prefix} ${type} ${JSON.stringify(payload)}`;
      } catch(_) {
        return `${prefix} ${type} ${String(payload)}`;
      }
    }

    return {
      debug: (type, payload) => { if (enabledDebug) console.debug(fmt(type, withRunId(payload))); },
      info:  (type, payload) => console.info(fmt(type, withRunId(payload))),
      warn:  (type, payload) => console.warn(fmt(type, withRunId(payload))),
      error: (type, payload) => console.error(fmt(type, withRunId(payload))),
    };
  }

  AP.Adapters.createConsoleLogger = createConsoleLogger;
})();
