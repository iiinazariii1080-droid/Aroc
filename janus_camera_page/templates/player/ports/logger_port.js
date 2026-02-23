(function(){
  'use strict';
  const AP = window.AutonomousPlayer;
  if (!AP) throw new Error('AutonomousPlayer namespace missing');

  /**
   * @typedef {{
   *  debug: (type: string, payload?: any) => void,
   *  info: (type: string, payload?: any) => void,
   *  warn: (type: string, payload?: any) => void,
   *  error: (type: string, payload?: any) => void,
   * }} LoggerPort
   * @typedef {LoggerPort} AP.Ports.LoggerPort
   */

  AP.Ports.LoggerPort = {};
})();
