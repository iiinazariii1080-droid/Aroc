(function(){
  'use strict';
  const AP = window.AutonomousPlayer;
  if (!AP) throw new Error('AutonomousPlayer namespace missing');

  /**
   * Domain-level event types. Single source of truth for connection/stream lifecycle.
   * No Janus/WebRTC/timer names — abstract. Used by ConnectionPolicy and StateMachine.
   * @readonly
   * @enum {string}
   */
  const DomainEventType = Object.freeze({
    USER_PLAY: 'user_play',
    USER_STOP: 'user_stop',
    STREAM_LOST: 'stream_lost',
    STREAM_RECOVERED: 'stream_recovered',
    ICE_FAILED: 'ice_failed',
    ICE_DISCONNECTED_GRACE_TIMEOUT: 'ice_disconnected_grace_timeout',
    MEDIA_SILENCE_TIMEOUT: 'media_silence_timeout',
    TRACK_MUTE_TIMEOUT: 'track_mute_timeout',
    TRACK_ENDED: 'track_ended',
    HANGUP: 'hangup',
    WEBRTC_DOWN: 'webrtc_down',
    SESSION_RESET: 'session_reset',
    JANUS_ERROR: 'janus_error',
    FPS_DROP: 'fps_drop',
    VIDEO_STALLED: 'video_stalled',
    RECONNECT_SCHEDULED: 'reconnect_scheduled',
    RECONNECT_SUCCESS: 'reconnect_success',
    RECONNECT_EXHAUSTED: 'reconnect_exhausted',
    SETTLE_TIME_UP: 'settle_time_up',
  });

  /**
   * Policy decision: what the controller should do after applying policy.
   * @readonly
   * @enum {string}
   */
  const PolicyAction = Object.freeze({
    NO_OP: 'no_op',
    REQUEST_RECOVERY: 'request_recovery',
    MARK_DEGRADED: 'mark_degraded',
  });

  AP.Core.DomainEventType = DomainEventType;
  AP.Core.PolicyAction = PolicyAction;
})();
