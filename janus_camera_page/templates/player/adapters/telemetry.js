/**
 * Telemetry adapter — collects WebRTC getStats() data and sends it
 * to the server's POST /telemetry endpoint.
 *
 * Usage:
 *   const tel = new AutonomousPlayer.Adapters.Telemetry(pc, { camera: 'color' });
 *   // Automatically sends ice_connected event, and periodic stats.
 *   // tel.stop()  to cancel.
 */
(function () {
  'use strict';

  const AP = window.AutonomousPlayer;
  if (!AP || !AP.Adapters) return;

  const TELEMETRY_URL = '/telemetry';
  const STATS_INTERVAL_MS = 30000;  // periodic stats every 30 s

  /**
   * @param {RTCPeerConnection} pc
   * @param {{ camera?: string, sessionId?: string }} opts
   */
  function Telemetry(pc, opts) {
    this._pc = pc;
    this._camera = (opts && opts.camera) || 'unknown';
    this._sessionId = (opts && opts.sessionId) || _uuid();
    this._iceStart = Date.now();
    this._firstFrameTs = null;
    this._timer = null;
    this._stopped = false;

    this._onIceChange = this._onIceChange.bind(this);
    pc.addEventListener('iceconnectionstatechange', this._onIceChange);
  }

  Telemetry.prototype._onIceChange = function () {
    var state = this._pc.iceConnectionState;
    if (state === 'connected' || state === 'completed') {
      var iceMs = Date.now() - this._iceStart;
      this._sendEvent('ice_connected', { ice_connect_ms: iceMs });
      this._startPeriodicStats();
      // Also try to grab selected candidate pair
      this._reportCandidates();
    } else if (state === 'failed') {
      this._sendEvent('ice_failed', {});
    }
  };

  Telemetry.prototype._reportCandidates = function () {
    var self = this;
    if (!this._pc.getStats) return;
    this._pc.getStats().then(function (stats) {
      var local = null, remote = null;
      stats.forEach(function (report) {
        if (report.type === 'candidate-pair' && report.state === 'succeeded') {
          stats.forEach(function (r2) {
            if (r2.id === report.localCandidateId) local = r2;
            if (r2.id === report.remoteCandidateId) remote = r2;
          });
        }
      });
      if (local || remote) {
        self._send({
          event: 'ice_connected',
          session_id: self._sessionId,
          camera: self._camera,
          local_candidate: local ? {
            type: local.candidateType,
            protocol: local.protocol,
            address: local.address || local.ip,
            port: local.port
          } : null,
          remote_candidate: remote ? {
            type: remote.candidateType,
            protocol: remote.protocol,
            address: remote.address || remote.ip,
            port: remote.port
          } : null
        });
      }
    }).catch(function () { /* ignore */ });
  };

  Telemetry.prototype._startPeriodicStats = function () {
    if (this._timer) return;
    var self = this;
    this._timer = setInterval(function () {
      if (self._stopped || !self._pc || self._pc.connectionState === 'closed') {
        self.stop();
        return;
      }
      self._collectStats();
    }, STATS_INTERVAL_MS);
  };

  Telemetry.prototype._collectStats = function () {
    var self = this;
    if (!this._pc.getStats) return;
    this._pc.getStats().then(function (stats) {
      var inbound = null;
      stats.forEach(function (report) {
        if (report.type === 'inbound-rtp' && report.kind === 'video') {
          inbound = report;
        }
      });
      if (!inbound) return;
      self._send({
        event: 'stats_report',
        session_id: self._sessionId,
        camera: self._camera,
        packets_received: inbound.packetsReceived,
        packets_lost: inbound.packetsLost,
        jitter: inbound.jitter,
        bytes_received: inbound.bytesReceived,
        frames_decoded: inbound.framesDecoded,
        frames_dropped: inbound.framesDropped
      });
    }).catch(function () { /* ignore */ });
  };

  Telemetry.prototype.reportFirstFrame = function () {
    if (!this._firstFrameTs) {
      this._firstFrameTs = Date.now();
      var ttff = this._firstFrameTs - this._iceStart;
      this._sendEvent('ice_connected', { time_to_first_frame_ms: ttff });
    }
  };

  Telemetry.prototype.stop = function () {
    this._stopped = true;
    if (this._timer) {
      clearInterval(this._timer);
      this._timer = null;
    }
    if (this._pc) {
      this._pc.removeEventListener('iceconnectionstatechange', this._onIceChange);
    }
  };

  Telemetry.prototype._sendEvent = function (event, extra) {
    var payload = {
      event: event,
      session_id: this._sessionId,
      camera: this._camera
    };
    for (var k in extra) {
      if (extra.hasOwnProperty(k)) payload[k] = extra[k];
    }
    this._send(payload);
  };

  Telemetry.prototype._send = function (payload) {
    try {
      var xhr = new XMLHttpRequest();
      xhr.open('POST', TELEMETRY_URL, true);
      xhr.setRequestHeader('Content-Type', 'application/json');
      xhr.send(JSON.stringify(payload));
    } catch (e) { /* swallow — telemetry is non-critical */ }
  };

  function _uuid() {
    return 'xxxxxxxx-xxxx-4xxx'.replace(/x/g, function () {
      return (Math.random() * 16 | 0).toString(16);
    });
  }

  AP.Adapters.Telemetry = Telemetry;
})();
