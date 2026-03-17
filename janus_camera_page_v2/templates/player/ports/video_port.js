(function(){
  'use strict';
  const AP = window.AutonomousPlayer;
  if (!AP) throw new Error('AutonomousPlayer namespace missing');

  /**
   * @typedef {Object} VideoPort
   * @property {(stream: MediaStream) => void} bindStream
   * @property {() => Promise<{ok:boolean, blocked:boolean, error?:any}>} ensurePlaying
   * @property {(handlers: {onTogglePlay?:Function, onRetry?:Function, onToggleStats?:Function}) => void} bindIntents
   * @property {(vm: {state:string, attempt:number, desiredPlaying:boolean, errCode?:string, autoplayBlocked?:boolean, debugText?:string}) => void} render
   * @property {(onFrame: () => void) => void} startFrameClock  // Calls onFrame when a new video frame is observed.
   * @property {(text: string) => void} setStatsText  // Used by StatsService callback to display WebRTC stats.
   * @typedef {VideoPort} AP.Ports.VideoPort
   */

  // Documentation-only port placeholder.
  AP.Ports.VideoPort = AP.Ports.VideoPort || {};
})();
