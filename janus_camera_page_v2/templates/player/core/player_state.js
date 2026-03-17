(function(){
  'use strict';
  const AP = window.AutonomousPlayer;
  if (!AP) throw new Error('AutonomousPlayer namespace missing');

  /**
   * @readonly
   * @enum {string}
   */
  const PlayerState = Object.freeze({
    IDLE: 'IDLE',
    CONNECTING: 'CONNECTING',
    PLAYING: 'PLAYING',
    RECONNECTING: 'RECONNECTING',
    ERROR: 'ERROR',
  });

  /**
   * Recovery severity expresses how hard we should escalate.
   * @readonly
   * @enum {number}
   */
  const RecoverySeverity = Object.freeze({
    SOFT: 1,
    MEDIUM: 2,
    HARD: 3,
  });

  /**
   * Recovery action is the concrete operation we will attempt.
   * @readonly
   * @enum {number}
   */
  const RecoveryAction = Object.freeze({
    SOFT_RESTART: 1,      // stop → watch
    REATTACH_PLUGIN: 2,   // detach streaming handle → attach again
    RECREATE_SESSION: 3,  // destroy janus session → create again
  });

  function statusTextFor(state, attempt, errCode){
    switch(state){
      case PlayerState.IDLE: return 'IDLE';
      case PlayerState.CONNECTING: return 'CONNECTING…';
      case PlayerState.PLAYING: return 'PLAYING';
      case PlayerState.RECONNECTING: return `RECONNECTING… (attempt ${attempt || 1})`;
      case PlayerState.ERROR: return `ERROR: ${errCode || 'unknown'}`;
      default: return String(state || 'UNKNOWN');
    }
  }

  AP.Core.PlayerState = PlayerState;
  AP.Core.RecoverySeverity = RecoverySeverity;
  AP.Core.RecoveryAction = RecoveryAction;
  AP.Core.statusTextFor = statusTextFor;
})();
