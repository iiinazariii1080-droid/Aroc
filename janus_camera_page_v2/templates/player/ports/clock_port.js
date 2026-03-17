(function(){
  'use strict';
  const AP = window.AutonomousPlayer;
  if (!AP) throw new Error('AutonomousPlayer namespace missing');

  /**
   * @typedef {{
   *  nowMs: () => number,
   *  setTimeout: (fn: Function, ms: number) => any,
   *  clearTimeout: (id: any) => void,
   *  setInterval: (fn: Function, ms: number) => any,
   *  clearInterval: (id: any) => void,
   *  [debugSnapshot]: () => { timeouts?: number, intervals?: number }  // Optional; used for debug panel.
   * }} ClockPort
   * @typedef {ClockPort} AP.Ports.ClockPort
   */

  AP.Ports.ClockPort = {};
})();
