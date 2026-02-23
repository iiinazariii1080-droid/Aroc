(function(){
  'use strict';
  /**
   * @deprecated Use StateMachineCanonical (state_machine_canonical.js) for runtime.
   * This file is kept only for run_core_tests duality guard. Not loaded in browser.
   */
  const AP = window.AutonomousPlayer;
  if (!AP) throw new Error('AutonomousPlayer namespace missing');

  const PlayerState = AP.Core.PlayerState;
  const DomainEventType = AP.Core.DomainEventType;

  /**
   * Auxiliary state machine: (currentState, domainEventType) -> nextState or null.
   * Simple transition map for tests/legacy. Runtime uses StateMachineCanonical (state_machine_canonical.js).
   *
   * @param {string} state current PlayerState
   * @param {string} eventType DomainEventType
   * @returns {string|null} next PlayerState or null if no transition
   */
  function transition(state, eventType){
    const S = PlayerState;
    const E = DomainEventType;

    switch (state) {
      case S.IDLE:
        if (eventType === E.USER_PLAY) return S.CONNECTING;
        break;
      case S.CONNECTING:
        if (eventType === E.USER_STOP) return S.IDLE;
        if (eventType === E.STREAM_RECOVERED) return S.PLAYING;
        if (eventType === E.RECONNECT_SCHEDULED) return S.RECONNECTING;
        break;
      case S.PLAYING:
        if (eventType === E.USER_STOP) return S.IDLE;
        if (eventType === E.STREAM_LOST || eventType === E.RECONNECT_SCHEDULED) return S.RECONNECTING;
        break;
      case S.RECONNECTING:
        if (eventType === E.USER_STOP) return S.IDLE;
        if (eventType === E.STREAM_RECOVERED || eventType === E.RECONNECT_SUCCESS) return S.PLAYING;
        if (eventType === E.RECONNECT_EXHAUSTED) return S.ERROR;
        break;
      case S.ERROR:
        if (eventType === E.USER_PLAY) return S.CONNECTING;
        break;
      default:
        break;
    }
    return null;
  }

  AP.Core.StateMachine = { transition };
})();
