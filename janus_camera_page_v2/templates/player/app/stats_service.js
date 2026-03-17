(function(){
  'use strict';
  const AP = window.AutonomousPlayer;
  if (!AP) throw new Error('AutonomousPlayer namespace missing');

  class StatsService {
    /**
     * @param {any} cfg
     * @param {any} clock
     * @param {any} logger
     * @param {any} streamingPort
     */
    constructor(cfg, clock, logger, streamingPort){
      this.cfg = cfg;
      this.clock = clock;
      this.log = logger;
      this.streaming = streamingPort;

      this._timer = null;
      this._prev = { t: 0, bytes: 0, packetsLost: 0, packetsReceived: 0, jbDelay: 0, jbEmitted: 0 };
      this._text = '';
      this._running = false;
      this._joystick = null;
    }

    /** @param {any} joystickService */
    setJoystickService(joystickService){
      this._joystick = joystickService || null;
    }

    start(onUpdate){
      if (this._running) return;
      this._running = true;
      const tick = async () => {
        if (!this._running) return;
        try {
          this._text = await this._collectText();
          onUpdate && onUpdate(this._text);
        } catch (e) {
          this.log.debug('stats_collect_error', { error: String(e) });
        }
      };
      tick();
      this._timer = this.clock.setInterval(tick, this.cfg.statsIntervalMs || 1000);
    }

    stop(){
      this._running = false;
      if (this._timer) {
        this.clock.clearInterval(this._timer);
        this._timer = null;
      }
    }

    async _collectText(){
      const pc = this.streaming.getPeerConnection();
      if (!pc) return 'No peer connection';
      const report = await pc.getStats();

      let inboundVideo = null;
      let selectedPair = null;

      report.forEach((stat) => {
        if (stat.type === 'inbound-rtp' && stat.kind === 'video') {
          if (!inboundVideo || (stat.bytesReceived || 0) > (inboundVideo.bytesReceived || 0)) inboundVideo = stat;
        }
        if (stat.type === 'candidate-pair' && stat.state === 'succeeded' && stat.nominated) {
          selectedPair = stat;
        }
      });

      const lines = [];
      const now = this.clock.nowMs();
      const prev = this._prev;
      const hasPrev = prev.t > 0;
      const dt = hasPrev ? Math.max(1, now - prev.t) : 0;

      // ── E2E latency (Camera→Client) — always first ──
      // E2E ≈ cameraJanus + jitterBuf + RTT/2 + decode (math unchanged)
      let rttMs = NaN;
      if (selectedPair) {
        rttMs = Number.isFinite(selectedPair.currentRoundTripTime) ? selectedPair.currentRoundTripTime * 1000 : NaN;
      }

      const jitterBufferDelay = inboundVideo ? inboundVideo.jitterBufferDelay : undefined;
      const jitterBufferEmittedCount = inboundVideo ? inboundVideo.jitterBufferEmittedCount : undefined;

      // Delta-based jitter buffer: per-interval average (not cumulative)
      let jitterBufMs = NaN;
      if (Number.isFinite(jitterBufferDelay) && Number.isFinite(jitterBufferEmittedCount)) {
        if (hasPrev && prev.jbEmitted > 0) {
          const dDelay = jitterBufferDelay - prev.jbDelay;
          const dCount = jitterBufferEmittedCount - prev.jbEmitted;
          if (dCount > 0) {
            jitterBufMs = (dDelay / dCount) * 1000;
          }
        } else if (jitterBufferEmittedCount > 0) {
          // First sample: use cumulative as fallback
          jitterBufMs = (jitterBufferDelay / jitterBufferEmittedCount) * 1000;
        }
      }

      const fpsFromCfg = Number.isFinite(this.cfg && this.cfg.cameraFramerateFps) ? this.cfg.cameraFramerateFps : NaN;
      const fpsFromStats = inboundVideo && Number.isFinite(inboundVideo.framesPerSecond) ? inboundVideo.framesPerSecond : NaN;
      const fps = Number.isFinite(fpsFromCfg) ? fpsFromCfg : fpsFromStats;

      let cameraJanusMs = NaN;
      if (Number.isFinite(fps) && fps > 0) {
        cameraJanusMs = (1000 / fps) / 2;
      }

      const decodeMs = Number.isFinite(this.cfg && this.cfg.decodeLatencyMs) ? this.cfg.decodeLatencyMs : 0;

      const camPart = Number.isFinite(cameraJanusMs) ? cameraJanusMs : 0;
      const jitterPart = Number.isFinite(jitterBufMs) ? jitterBufMs : 0;
      const netPart = Number.isFinite(rttMs) ? (rttMs / 2) : 0;
      const decodePart = decodeMs;

      const e2eMs = camPart + jitterPart + netPart + decodePart;
      lines.push(`E2E      ${Number.isFinite(e2eMs) && e2eMs > 0 ? e2eMs.toFixed(0) + ' ms' : '\u2014'}`);

      // ── FPS ──
      const fpsDisplay = Number.isFinite(fpsFromStats) ? Math.round(fpsFromStats) : '\u2014';
      lines.push(`FPS      ${fpsDisplay}`);

      // ── Bitrate ──
      if (inboundVideo && Number.isFinite(inboundVideo.bytesReceived)) {
        let bitrateStr = '\u2014';
        if (hasPrev) {
          const dbytes = Math.max(0, inboundVideo.bytesReceived - prev.bytes);
          const bitrateKbps = (dbytes * 8) / dt; // kbps because dt is ms
          bitrateStr = bitrateKbps >= 1000
            ? (bitrateKbps / 1000).toFixed(1) + ' Mbps'
            : bitrateKbps.toFixed(0) + ' kbps';
        }
        lines.push(`Bitrate  ${bitrateStr}`);
      }

      // ── Packet loss rate (interval) ──
      if (inboundVideo) {
        const curLost = inboundVideo.packetsLost || 0;
        const curRecv = inboundVideo.packetsReceived || 0;
        if (hasPrev) {
          const dLost = Math.max(0, curLost - prev.packetsLost);
          const dRecv = Math.max(0, curRecv - prev.packetsReceived);
          const dTotal = dLost + dRecv;
          const lossPct = dTotal > 0 ? (dLost / dTotal) * 100 : 0;
          lines.push(`Loss     ${lossPct.toFixed(1)}%`);
        }
      }

      // ── Joystick E2E latency + jitter (Browser→Robot) ──
      if (this._joystick) {
        const joyMs = this._joystick.joyE2eMs;
        const joyJitter = this._joystick.joyJitterMs;
        const e2eStr = Number.isFinite(joyMs) ? joyMs.toFixed(0) + ' ms' : '\u2014';
        const jitStr = Number.isFinite(joyJitter) ? '\u00b1' + joyJitter.toFixed(0) : '';
        lines.push(`Joy E2E  ${e2eStr}${jitStr ? ' ' + jitStr : ''}`);
      }

      // ── Save snapshot for delta computation ──
      this._prev = {
        t: now,
        bytes: inboundVideo ? (inboundVideo.bytesReceived || 0) : prev.bytes,
        packetsLost: inboundVideo ? (inboundVideo.packetsLost || 0) : prev.packetsLost,
        packetsReceived: inboundVideo ? (inboundVideo.packetsReceived || 0) : prev.packetsReceived,
        jbDelay: Number.isFinite(jitterBufferDelay) ? jitterBufferDelay : prev.jbDelay,
        jbEmitted: Number.isFinite(jitterBufferEmittedCount) ? jitterBufferEmittedCount : prev.jbEmitted,
      };

      return lines.join('\n');
    }
  }

  AP.App.StatsService = StatsService;
})();
