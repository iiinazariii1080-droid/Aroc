(function(){
  'use strict';
  const AP = window.AutonomousPlayer;
  if (!AP) throw new Error('AutonomousPlayer namespace missing');

  const RecoveryReason = AP.Core.RecoveryReason;
  const RecoverySeverity = AP.Core.RecoverySeverity;

  /**
   * Policy: reason -> default severity (DRY).
   * App-layer policy; core stays pure.
   *
   * You may override severity explicitly in requestRecovery(reason, severity).
   */
  const DefaultSeverityByReason = Object.freeze({
    [RecoveryReason.WEBRTC_DOWN]: RecoverySeverity.MEDIUM,
    [RecoveryReason.ICE_FAILED]: RecoverySeverity.HARD,
    [RecoveryReason.ICE_DISCONNECTED_GRACE]: RecoverySeverity.MEDIUM,
    [RecoveryReason.HANGUP]: RecoverySeverity.MEDIUM,
    [RecoveryReason.NO_FRAMES]: RecoverySeverity.MEDIUM,
    [RecoveryReason.TRACK_MUTED]: RecoverySeverity.MEDIUM,
    [RecoveryReason.JANUS_ERROR]: RecoverySeverity.MEDIUM,
    [RecoveryReason.ALREADY_WATCHING]: RecoverySeverity.HARD,
    [RecoveryReason.SESSION_RESET]: RecoverySeverity.HARD,
    [RecoveryReason.FPS_DROP]: RecoverySeverity.MEDIUM,
    [RecoveryReason.VIDEO_STALLED]: RecoverySeverity.MEDIUM,
    [RecoveryReason.TAB_RESUME_STALE]: RecoverySeverity.HARD,
    [RecoveryReason.NETWORK_RESTORED]: RecoverySeverity.HARD,
  });

  function defaultSeverityForReason(reason, fallback){
    const r = String(reason || '');
    return DefaultSeverityByReason[r] || fallback || RecoverySeverity.SOFT;
  }

  AP.App.RecoveryPolicy = {
    DefaultSeverityByReason,
    defaultSeverityForReason,
  };
})();
