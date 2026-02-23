(function(){
  'use strict';
  const AP = window.AutonomousPlayer;
  if (!AP) throw new Error('AutonomousPlayer namespace missing');

  const PlayerState = AP.Core.PlayerState;

  /**
   * Thrown when InvariantGate.check detects an impossible state (SAFETY_LAWS L17).
   * Controller must catch and fail-closed: log L23, transition to ERROR, CANCEL_ALL_TIMERS.
   */
  class InvariantViolation extends Error {
    constructor(id, message){
      super(message || `Invariant violation: ${id}`);
      this.name = 'InvariantViolation';
      this.id = id;
    }
  }

  /**
   * Hard stop on invalid snapshot. L4–L7 as far as checkable from snapshot.
   * Pure: no I/O, only throws. Logging happens in controller on catch.
   *
   * L4: PLAYING ⇒ webrtcUp === true && firstFrameReceived === true
   * L5 (snapshot): RECONNECTING ⇒ reconnectAttempts >= 1 when defined.
   * L6 (snapshot): IDLE/ERROR ⇒ webrtcUp === false && firstFrameReceived === false.
   *
   * @param {{ state: string, webrtcUp?: boolean, firstFrameReceived?: boolean, reconnectAttempts?: number }} snapshot
   * @throws {InvariantViolation}
   */
  function check(snapshot){
    if (!snapshot) return;
    if (snapshot.state === PlayerState.PLAYING) {
      if (snapshot.webrtcUp !== true) {
        throw new InvariantViolation('L4', 'PLAYING state requires webrtcUp=true');
      }
      if (snapshot.firstFrameReceived !== true) {
        throw new InvariantViolation('L4', 'PLAYING state requires firstFrameReceived=true');
      }
    }
    if (snapshot.state === PlayerState.RECONNECTING) {
      const attempts = snapshot.reconnectAttempts;
      if (attempts == null || attempts < 1) {
        throw new InvariantViolation('L5', 'RECONNECTING state requires reconnectAttempts>=1');
      }
    }
    if (snapshot.state === PlayerState.IDLE || snapshot.state === PlayerState.ERROR) {
      if (snapshot.webrtcUp === true) {
        throw new InvariantViolation('L6', `${snapshot.state} state requires webrtcUp=false`);
      }
      if (snapshot.firstFrameReceived === true) {
        throw new InvariantViolation('L6', `${snapshot.state} state requires firstFrameReceived=false`);
      }
    }
  }

  AP.Core.InvariantViolation = InvariantViolation;
  AP.Core.InvariantGate = { check };
})();
