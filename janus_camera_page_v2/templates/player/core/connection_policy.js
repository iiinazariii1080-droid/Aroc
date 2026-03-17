(function(){
  'use strict';
  const AP = window.AutonomousPlayer;
  if (!AP) throw new Error('AutonomousPlayer namespace missing');

  const DomainEventType = AP.Core.DomainEventType;
  const PolicyAction = AP.Core.PolicyAction;
  const RecoveryReason = AP.Core.RecoveryReason;
  const RecoverySeverity = AP.Core.RecoverySeverity;
  const PlayerState = AP.Core.PlayerState;

  /**
   * Definition of Connected (canonical).
   * connected := webrtcUp === true AND firstVideoFrameReceived === true
   * (dataChannelOpen when applicable; not used in streaming plugin today).
   * ICE state is NOT part of the success criterion.
   *
   * @param {{ webrtcUp: boolean, firstFrameReceived: boolean }} snapshot
   * @returns {boolean}
   */
  function isConnected(snapshot){
    return !!(snapshot && snapshot.webrtcUp && snapshot.firstFrameReceived);
  }

  /**
   * ConnectionPolicy: pure function. Decides action from domain event + snapshot.
   * No timers, no I/O. Timers only emit events; this interprets them.
   *
   * @param {string} eventType one of DomainEventType
   * @param {{
   *   state: string,
   *   webrtcUp: boolean,
   *   firstFrameReceived: boolean,
   *   iceState: string,
   *   lastFrameAgeMs: number,
   *   inReconnectInFlight: boolean,
   *   desiredPlaying: boolean
   * }} snapshot
   * @returns {{ action: string, reason?: string, severity?: number }}
   */
  function decide(eventType, snapshot){
    const state = snapshot && snapshot.state;
    const webrtcUp = !!(snapshot && snapshot.webrtcUp);
    const firstFrame = !!(snapshot && snapshot.firstFrameReceived);
    const mediaFlowing = firstFrame;
    const inReconnect = !!(snapshot && snapshot.inReconnectInFlight);
    const desired = !!(snapshot && snapshot.desiredPlaying);

    if (!desired && state !== PlayerState.CONNECTING && state !== PlayerState.RECONNECTING) {
      return { action: PolicyAction.NO_OP };
    }

    switch (eventType) {
      case DomainEventType.ICE_FAILED:
        // ICE failed is terminal for the connection — always escalate to HARD recovery.
        // Unlike ICE disconnected (transient), ICE failed means no candidate pairs work.
        // MARK_DEGRADED would leave the player stuck without video.
        return { action: PolicyAction.REQUEST_RECOVERY, reason: RecoveryReason.ICE_FAILED, severity: RecoverySeverity.HARD };

      case DomainEventType.ICE_DISCONNECTED_GRACE_TIMEOUT: {
        // If frames are flowing but stale (>2s old), treat as real disconnection
        const frameAge = snapshot && snapshot.lastFrameAgeMs;
        const staleFrame = typeof frameAge === 'number' && frameAge > 2000;
        if (mediaFlowing && !staleFrame) return { action: PolicyAction.MARK_DEGRADED };
        return { action: PolicyAction.REQUEST_RECOVERY, reason: RecoveryReason.ICE_DISCONNECTED_GRACE, severity: RecoverySeverity.MEDIUM };
      }

      case DomainEventType.MEDIA_SILENCE_TIMEOUT:
        return { action: PolicyAction.REQUEST_RECOVERY, reason: RecoveryReason.NO_FRAMES, severity: RecoverySeverity.MEDIUM };

      case DomainEventType.TRACK_MUTE_TIMEOUT:
        return { action: PolicyAction.REQUEST_RECOVERY, reason: RecoveryReason.TRACK_MUTED, severity: RecoverySeverity.MEDIUM };

      case DomainEventType.TRACK_ENDED:
        return { action: PolicyAction.REQUEST_RECOVERY, reason: RecoveryReason.NO_FRAMES, severity: RecoverySeverity.MEDIUM };

      case DomainEventType.HANGUP:
        // Janus sometimes reports ICE failures as a hangup reason (without an ICE_STATE=failed callback).
        // Treat that as a HARD failure unless media is demonstrably still flowing (then just mark degraded).
        if (snapshot && String(snapshot.hangupReason || '').toLowerCase().includes('ice') && mediaFlowing) {
          return { action: PolicyAction.MARK_DEGRADED };
        }
        if (snapshot && String(snapshot.hangupReason || '').toLowerCase().includes('ice')) {
          return { action: PolicyAction.REQUEST_RECOVERY, reason: RecoveryReason.ICE_FAILED, severity: RecoverySeverity.HARD };
        }
        return { action: PolicyAction.REQUEST_RECOVERY, reason: RecoveryReason.HANGUP, severity: RecoverySeverity.MEDIUM };

      case DomainEventType.WEBRTC_DOWN:
        // If WebRTC goes down due to ICE failure and we have no media flowing, escalate immediately.
        if (snapshot && String(snapshot.webrtcDownReason || '').toLowerCase().includes('ice') && mediaFlowing) {
          return { action: PolicyAction.MARK_DEGRADED };
        }
        if (snapshot && String(snapshot.webrtcDownReason || '').toLowerCase().includes('ice')) {
          return { action: PolicyAction.REQUEST_RECOVERY, reason: RecoveryReason.ICE_FAILED, severity: RecoverySeverity.HARD };
        }
        return { action: PolicyAction.REQUEST_RECOVERY, reason: RecoveryReason.WEBRTC_DOWN, severity: RecoverySeverity.MEDIUM };

      case DomainEventType.SESSION_RESET:
        return { action: PolicyAction.REQUEST_RECOVERY, reason: RecoveryReason.SESSION_RESET, severity: RecoverySeverity.HARD };

      case DomainEventType.JANUS_ERROR:
        return { action: PolicyAction.REQUEST_RECOVERY, reason: RecoveryReason.JANUS_ERROR, severity: RecoverySeverity.MEDIUM };

      case DomainEventType.FPS_DROP:
        return { action: PolicyAction.REQUEST_RECOVERY, reason: RecoveryReason.FPS_DROP, severity: RecoverySeverity.MEDIUM };

      case DomainEventType.VIDEO_STALLED:
        return { action: PolicyAction.REQUEST_RECOVERY, reason: RecoveryReason.VIDEO_STALLED, severity: RecoverySeverity.MEDIUM };

      default:
        return { action: PolicyAction.NO_OP };
    }
  }

  AP.Core.ConnectionPolicy = {
    isConnected,
    decide,
  };
})();
