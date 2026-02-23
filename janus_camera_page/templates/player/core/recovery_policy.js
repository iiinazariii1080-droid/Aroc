(function(){
  'use strict';
  const AP = window.AutonomousPlayer;
  if (!AP) throw new Error('AutonomousPlayer namespace missing');

  const RecoveryAction = AP.Core.RecoveryAction;
  const RecoverySeverity = AP.Core.RecoverySeverity;

  /**
   * Decide next recovery action based on attempt count and policy.
   * Pure function: no I/O. cfg must be immutable (read-only) for deterministic behavior.
   *
   * @param {number} attemptOneBased
   * @param {number} severity one of RecoverySeverity
   * @param {{maxWatchRetries:number, maxReattachRetries:number}} cfg - MUST be immutable
   * @returns {number} RecoveryAction
   */
  function decideRecoveryAction(attemptOneBased, severity, cfg){
    const attempt = Math.max(1, Math.trunc(attemptOneBased || 1));
    const sev = Math.max(RecoverySeverity.SOFT, Math.min(RecoverySeverity.HARD, Math.trunc(severity || RecoverySeverity.SOFT)));

    if (sev >= RecoverySeverity.HARD) return RecoveryAction.RECREATE_SESSION;

    const watchMax = Math.max(0, Math.trunc(cfg?.maxWatchRetries ?? 3));
    const reattachMax = Math.max(0, Math.trunc(cfg?.maxReattachRetries ?? 2));

    if (attempt <= watchMax) return RecoveryAction.SOFT_RESTART;
    if (attempt <= watchMax + reattachMax) return RecoveryAction.REATTACH_PLUGIN;
    return RecoveryAction.RECREATE_SESSION;
  }

  AP.Core.decideRecoveryAction = decideRecoveryAction;
})();
