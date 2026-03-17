(function(){
  'use strict';
  const AP = window.AutonomousPlayer;
  if (!AP) throw new Error('AutonomousPlayer namespace missing');

  /**
   * PlayerErrorCode: stable, DRY catalog for UI + logs.
   * Keep these as string literals because they surface in the status pill.
   * @readonly
   * @enum {string}
   */
  const PlayerErrorCode = Object.freeze({
    BOOT_FAILED: 'boot_failed',
    CONNECT_FAILED: 'connect_failed',
    AUTOPLAY_BLOCKED: 'autoplay_blocked',
    RECONNECT_EXHAUSTED: 'reconnect_exhausted',
    INVARIANT_VIOLATION: 'invariant_violation',
    ACTION_FAILED: 'action_failed',
    UNKNOWN_ERROR: 'unknown_error',
  });

  /**
   * RecoveryReason: stable catalog for recovery triggers.
   * @readonly
   * @enum {string}
   */
  const RecoveryReason = Object.freeze({
    WEBRTC_DOWN: 'webrtc_down',
    ICE_FAILED: 'ice_failed',
    ICE_DISCONNECTED_GRACE: 'ice_disconnected_grace',
    HANGUP: 'hangup',
    NO_FRAMES: 'no_frames',
    TRACK_MUTED: 'track_muted',
    JANUS_ERROR: 'janus_error',
    ALREADY_WATCHING: 'already_watching',
    SESSION_RESET: 'session_reset',
    FPS_DROP: 'fps_drop',
    VIDEO_STALLED: 'video_stalled',
    TAB_RESUME_STALE: 'tab_resume_stale',
    NETWORK_RESTORED: 'network_restored',
  });

  /** Maximum reconnect attempts before transitioning to ERROR (L12). Config may clamp within [3, 50]. */
  const MAX_RECONNECT_ATTEMPTS = 12;

  AP.Core.PlayerErrorCode = PlayerErrorCode;
  AP.Core.RecoveryReason = RecoveryReason;
  AP.Core.MAX_RECONNECT_ATTEMPTS = MAX_RECONNECT_ATTEMPTS;
})();
