(function(){
  'use strict';
  const AP = window.AutonomousPlayer;
  if (!AP) throw new Error('AutonomousPlayer namespace missing');

  const PlayerState = AP.Core.PlayerState;

  /**
   * Authoritative runtime state machine: (event, snapshot) -> { next, actions }.
   * Used by PlayerController, InvariantGate, and action execution. Legacy state_machine_legacy.js is for tests only (duality guard).
   */

  /**
   * Canonical event types for the state machine. Align with DomainEventType where applicable.
   * Event is { type: string, reason?: string, severity?: number, ... }.
   */
  const EventType = Object.freeze({
    PLAY_REQUEST: 'PLAY_REQUEST',
    STOP_REQUEST: 'STOP_REQUEST',
    WEBRTC_UP: 'WEBRTC_UP',
    WEBRTC_DOWN: 'WEBRTC_DOWN',
    WEBRTC_REPORT: 'WEBRTC_REPORT',
    ICE_REPORT: 'ICE_REPORT',
    ICE_FAILED: 'ICE_FAILED',
    STREAM_RECOVERED: 'STREAM_RECOVERED',
    RECONNECT_SCHEDULED: 'RECONNECT_SCHEDULED',
    RECONNECT_SUCCESS: 'RECONNECT_SUCCESS',
    RECONNECT_EXHAUSTED: 'RECONNECT_EXHAUSTED',
    RESET: 'RESET',
    CONNECT_FAILED: 'CONNECT_FAILED',
    FORCE_ERROR: 'FORCE_ERROR',
    TRACK_MUTED: 'TRACK_MUTED',
    TRACK_UNMUTED: 'TRACK_UNMUTED',
    TRACK_READY: 'TRACK_READY',
    STREAMING_OFFER_RECEIVED: 'STREAMING_OFFER_RECEIVED',
    POLICY_MARK_DEGRADED: 'POLICY_MARK_DEGRADED',
    FIRST_FRAME_RECEIVED: 'FIRST_FRAME_RECEIVED',
    RECOVERY_ATTEMPT_STARTED: 'RECOVERY_ATTEMPT_STARTED',
  });

  /**
   * Action types emitted by the state machine. Executor performs side-effects.
   * Action is { type: string, message?: string, reason?: string, severity?: number, trackId?: string }.
   */
  const ActionType = Object.freeze({
    START_JANUS: 'START_JANUS',
    STOP_JANUS: 'STOP_JANUS',
    STOP_STREAMING: 'STOP_STREAMING',
    START_RECONNECT_TIMER: 'START_RECONNECT_TIMER',
    CANCEL_ALL_TIMERS: 'CANCEL_ALL_TIMERS',
    LOG: 'LOG',
    RENDER: 'RENDER',
    ARM_ICE_GRACE: 'ARM_ICE_GRACE',
    CANCEL_ICE_GRACE: 'CANCEL_ICE_GRACE',
    ARM_TRACK_MUTE_TIMER: 'ARM_TRACK_MUTE_TIMER',
    DISARM_TRACK_MUTE_TIMER: 'DISARM_TRACK_MUTE_TIMER',
    MARK_DEGRADED: 'MARK_DEGRADED',
    BIND_STREAM: 'BIND_STREAM',
    START_RECONNECT_SETTLE: 'START_RECONNECT_SETTLE',
  });

  /**
   * Snapshot: minimal state for transition and invariant checks.
   * @typedef {{ state: string, generation: number, reconnectAttempts: number, webrtcUp: boolean, firstFrameReceived: boolean, iceState?: string }} Snapshot
   */

  /**
   * Pure state machine: (event, snapshot) -> { next, actions }.
   * No assignments, no I/O. Unknown transition returns fail-closed (ERROR + LOG).
   *
   * @param {{ type: string, reason?: string, severity?: number }} event
   * @param {Snapshot} snap
   * @returns {{ next: Snapshot, actions: Array<{ type: string, message?: string, reason?: string, severity?: number }> }}
   */
  function transition(event, snap){
    const S = PlayerState;
    const E = EventType;
    const A = ActionType;
    const type = event && event.type;

    if (!snap || typeof snap.state !== 'string') {
      return failClosed(snap, 'Invalid snapshot');
    }

    if (type === E.FORCE_ERROR) {
      return failClosed(snap, event.reason || 'Force error', true);
    }

    switch (snap.state) {
      case S.IDLE:
        if (type === E.PLAY_REQUEST) {
          return {
            next: {
              state: S.CONNECTING,
              generation: (snap.generation || 0) + 1,
              reconnectAttempts: snap.reconnectAttempts || 0,
              webrtcUp: false,
              firstFrameReceived: false,
              iceState: 'new',
            },
            actions: [{ type: A.START_JANUS }, { type: A.RENDER }],
          };
        }
        break;

      case S.CONNECTING:
        if (type === E.STOP_REQUEST) {
          return toIdle(snap, [A.CANCEL_ALL_TIMERS, A.STOP_STREAMING]);
        }
        if (type === E.STREAM_RECOVERED) {
          return {
            next: {
              state: S.PLAYING,
              generation: snap.generation,
              reconnectAttempts: 0,
              webrtcUp: true,
              firstFrameReceived: snap.firstFrameReceived || false,
              iceState: snap.iceState || 'new',
            },
            actions: [{ type: A.CANCEL_ALL_TIMERS }, { type: A.RENDER }],
          };
        }
        if (type === E.RECONNECT_SCHEDULED || type === E.ICE_FAILED) {
          return {
            next: {
              state: S.RECONNECTING,
              generation: snap.generation,
              reconnectAttempts: (snap.reconnectAttempts || 0) + 1,
              webrtcUp: snap.webrtcUp || false,
              firstFrameReceived: snap.firstFrameReceived || false,
              iceState: snap.iceState || 'new',
            },
            actions: [{ type: A.START_RECONNECT_TIMER, reason: event.reason, severity: event.severity }],
          };
        }
        if (type === E.CONNECT_FAILED) {
          return failClosed(snap, event.reason || 'Connect failed');
        }
        if (type === E.STREAMING_OFFER_RECEIVED) {
          return reportNext(snap, {}, []);
        }
        return handleActiveCommon(event, snap) || failClosed(snap, 'Invalid transition');

      case S.PLAYING:
        if (type === E.STOP_REQUEST) {
          return toIdle(snap, [A.CANCEL_ALL_TIMERS, A.STOP_STREAMING]);
        }
        if (type === E.PLAY_REQUEST) {
          return { next: { ...snap }, actions: [{ type: A.RENDER }] };
        }
        if (type === E.RECONNECT_SUCCESS || type === E.STREAM_RECOVERED) {
          return { next: { ...snap }, actions: [{ type: A.RENDER }] };
        }
        if (type === E.WEBRTC_DOWN || type === E.RECONNECT_SCHEDULED || type === E.ICE_FAILED) {
          return {
            next: {
              state: S.RECONNECTING,
              generation: snap.generation,
              reconnectAttempts: (snap.reconnectAttempts || 0) + 1,
              webrtcUp: false,
              firstFrameReceived: snap.firstFrameReceived || false,
              iceState: snap.iceState || 'new',
            },
            actions: [{ type: A.START_RECONNECT_TIMER, reason: event.reason, severity: event.severity }],
          };
        }
        if (type === E.STREAMING_OFFER_RECEIVED) {
          return reportNext(snap, {}, [A.RENDER]);
        }
        return handleActiveCommon(event, snap) || failClosed(snap, 'Invalid transition');

      case S.RECONNECTING:
        if (type === E.STOP_REQUEST) {
          return toIdle(snap, [A.CANCEL_ALL_TIMERS, A.STOP_STREAMING]);
        }
        if (type === E.RECONNECT_SCHEDULED) {
          return {
            next: { ...snap, reconnectAttempts: Math.max(1, snap.reconnectAttempts || 0) },
            actions: [],
          };
        }
        if (type === E.STREAM_RECOVERED || type === E.RECONNECT_SUCCESS) {
          return {
            next: {
              state: S.PLAYING,
              generation: snap.generation,
              reconnectAttempts: 0,
              webrtcUp: true,
              firstFrameReceived: true,
              iceState: snap.iceState || 'new',
            },
            actions: [{ type: A.RENDER }],
          };
        }
        if (type === E.RECONNECT_EXHAUSTED) {
          return failClosed(snap, event.reason || 'Reconnect exhausted');
        }
        if (type === E.STREAMING_OFFER_RECEIVED) {
          return reportNext(snap, {}, [{ type: A.START_RECONNECT_SETTLE }]);
        }
        if (type === E.RECOVERY_ATTEMPT_STARTED) {
          return reportNext(snap, { webrtcUp: false, firstFrameReceived: false }, []);
        }
        return handleActiveCommon(event, snap) || failClosed(snap, 'Invalid transition');

      case S.ERROR:
        if (type === E.RESET || type === E.PLAY_REQUEST) {
          return {
            next: {
              state: S.CONNECTING,
              generation: (snap.generation || 0) + 1,
              reconnectAttempts: 0,
              webrtcUp: false,
              firstFrameReceived: false,
              iceState: 'new',
            },
            actions: [{ type: A.CANCEL_ALL_TIMERS }, { type: A.START_JANUS }, { type: A.RENDER }],
          };
        }
        break;

      default:
        break;
    }

    return failClosed(snap, 'Invalid transition');
  }

  /**
   * Shared event handling for active states (CONNECTING, PLAYING, RECONNECTING).
   * Returns transition result or null if event is not handled here.
   */
  function handleActiveCommon(event, snap){
    const E = EventType;
    const A = ActionType;
    const type = event && event.type;

    if (type === E.WEBRTC_REPORT) {
      return reportNext(snap, { webrtcUp: !!event.webrtcUp }, [A.RENDER]);
    }
    if (type === E.ICE_REPORT) {
      const iceState = String(event.iceState != null ? event.iceState : snap.iceState || 'new');
      const actions = iceState === 'disconnected' ? [{ type: A.ARM_ICE_GRACE }, { type: A.RENDER }]
        : (iceState === 'connected' || iceState === 'completed') ? [{ type: A.CANCEL_ICE_GRACE }, { type: A.RENDER }]
          : [{ type: A.RENDER }];
      return { next: { ...snap, iceState }, actions };
    }
    if (type === E.TRACK_MUTED && event.trackId != null) {
      return reportNext(snap, {}, [{ type: A.ARM_TRACK_MUTE_TIMER, trackId: String(event.trackId) }, { type: A.RENDER }]);
    }
    if (type === E.TRACK_UNMUTED && event.trackId != null) {
      return reportNext(snap, {}, [{ type: A.DISARM_TRACK_MUTE_TIMER, trackId: String(event.trackId) }, { type: A.RENDER }]);
    }
    if (type === E.TRACK_READY) {
      return reportNext(snap, {}, [{ type: A.BIND_STREAM }, { type: A.RENDER }]);
    }
    if (type === E.POLICY_MARK_DEGRADED) {
      return reportNext(snap, {}, [{ type: A.MARK_DEGRADED }, { type: A.RENDER }]);
    }
    if (type === E.FIRST_FRAME_RECEIVED) {
      return reportNext(snap, { firstFrameReceived: true }, [A.RENDER]);
    }
    return null;
  }

  /** Report-only transition: update snapshot fields, optional actions. Preserves state and iceState unless overridden. */
  function reportNext(snap, overrides, actions){
    const next = Object.assign({}, snap, overrides);
    const mapped = actions.map((a) => (typeof a === 'string' ? { type: a } : a));
    return { next, actions: mapped };
  }

  function toIdle(snap, actions){
    const S = PlayerState;
    const A = ActionType;
    const mapped = actions.map((a) => (typeof a === 'string' ? { type: a } : a));
    mapped.push({ type: A.RENDER });
    return {
      next: {
        state: S.IDLE,
        generation: snap.generation,
        reconnectAttempts: 0,
        webrtcUp: false,
        firstFrameReceived: false,
        iceState: 'new',
      },
      actions: mapped,
    };
  }

  function failClosed(snap, message, bumpGeneration){
    const S = PlayerState;
    const A = ActionType;
    const gen = (snap && snap.generation) != null ? snap.generation : 0;
    return {
      next: {
        state: S.ERROR,
        generation: bumpGeneration ? gen + 1 : gen,
        reconnectAttempts: snap && snap.reconnectAttempts,
        webrtcUp: false,
        firstFrameReceived: false,
        iceState: 'new',
      },
      actions: [{ type: A.LOG, message: message || 'Invalid transition' }, { type: A.CANCEL_ALL_TIMERS }, { type: A.STOP_STREAMING }, { type: A.RENDER }],
    };
  }

  AP.Core.EventType = EventType;
  AP.Core.ActionType = ActionType;
  AP.Core.StateMachineCanonical = { transition };
})();
