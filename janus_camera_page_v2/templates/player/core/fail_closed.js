(function(){
  'use strict';
  const AP = window.AutonomousPlayer;
  if (!AP) throw new Error('AutonomousPlayer namespace missing');

  /**
   * Fail-closed behavior table. When any of these situations occur, we do not retry indefinitely;
   * we stop, reset, or ignore as specified.
   *
   * | Situation                    | Action                                              |
   * | N reconnect failures in a row| HARD ERROR (onExhausted → _fail(RECONNECT_EXHAUSTED))|
   * | Invariant violated          | Immediate transition to ERROR + INVARIANT_VIOLATION  |
   * | Unknown/illegal transition  | RESET: transition to ERROR + _stopAll (I6)           |
   * | Inconsistent snapshot       | Fail-closed, do not continue                         |
   * | Duplicate critical event    | IGNORE + log (EVENT_DROPPED or RECONNECT_ABORTED)    |
   *
   * No unbounded "try again" without counter/limit (ReconnectCoordinator has maxReconnectAttempts).
   */
  const FAIL_CLOSED_TABLE = Object.freeze({
    RECONNECT_EXHAUSTED: 'HARD ERROR',
    INVARIANT_VIOLATION: 'IMMEDIATE STOP (ERROR)',
    ILLEGAL_TRANSITION: 'RESET (ERROR + _stopAll)',
    DUPLICATE_EVENT: 'IGNORE + log',
  });

  AP.Core.FailClosed = FAIL_CLOSED_TABLE;
})();
