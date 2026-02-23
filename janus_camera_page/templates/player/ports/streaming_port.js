(function(){
  'use strict';
  const AP = window.AutonomousPlayer;
  if (!AP) throw new Error('AutonomousPlayer namespace missing');

  /**
   * Domain-level events emitted by the streaming adapter.
   * @typedef {{type: string, payload?: any, token?: number}} StreamEvent
   *
   * Expected event types:
   *  - 'ICE_STATE' {state: string}
   *  - 'WEBRTC_STATE' {up: boolean, reason?: string}
   *  - 'HANGUP' {reason: string}
   *  - 'TRACK' {track: MediaStreamTrack, on: boolean}
   *  - 'TRACK_ENDED' {trackId, mid, kind} (event-driven stream loss)
   *  - 'ERROR' {where: string, error: any, error_code?: number}
   *
   * Adapter must not trigger retry/reconnect; only emit events. One active handle epoch (gen).
   */

  /**
   * @typedef {{
   *  init: (rtcConfig: any) => Promise<void>,
   *  ensureReady: () => Promise<void>,
   *  listStreams: () => Promise<any[]>,
   *  watch: (streamId: number) => Promise<void>,
   *  stop: () => Promise<void>,
   *  detach: () => Promise<void>,
   *  recreate: (rtcConfig: any) => Promise<void>,
   *  getPeerConnection: () => (RTCPeerConnection|null),
   *  getInboundStream: () => MediaStream,
   *  setEventSink: (sink: (ev: StreamEvent) => void, getToken?: () => number) => void,
   * }} StreamingPort
   * @typedef {StreamingPort} AP.Ports.StreamingPort
   */

  AP.Ports.StreamingPort = {};
})();
