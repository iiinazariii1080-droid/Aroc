(function(){
  'use strict';
  const AP = window.AutonomousPlayer;
  if (!AP) throw new Error('AutonomousPlayer namespace missing');

  /**
   * Deterministic jitter from (seed, attempt). No Math.random. xorshift32 step then map to [-jitterAmp, jitterAmp].
   */
  function deterministicJitter(seed, attempt, jitterAmp){
    let s = (((seed || 0) * 31 + attempt) >>> 0);
    if (s === 0) s = 1;
    s ^= s << 13;
    s ^= s >>> 17;
    s ^= s << 5;
    s >>>= 0;
    const u = s / 0xFFFFFFFF;
    return Math.floor((u * 2 - 1) * jitterAmp);
  }

  /**
   * Exponential backoff with optional deterministic jitter (bounded). No Math.random in core (L10).
   * When jitterRatio > 0, pass jitterSeed so jitter is reproducible; otherwise jitter is 0.
   * cfg must be immutable (read-only).
   *
   * @param {number} attemptOneBased
   * @param {{backoffBaseMs:number, backoffFactor:number, backoffMinMs:number, backoffMaxMs:number, backoffJitterRatio:number}} cfg - MUST be immutable
   * @param {number} [jitterSeed] - optional; when jitterRatio > 0, same (attempt, cfg, jitterSeed) => same delay
   */
  function computeBackoffMs(attemptOneBased, cfg, jitterSeed){
    const attempt = Math.max(1, Math.trunc(attemptOneBased || 1));
    const base = Number(cfg?.backoffBaseMs ?? 500);
    const factor = Number(cfg?.backoffFactor ?? 1.8);
    const min = Number(cfg?.backoffMinMs ?? 250);
    const max = Number(cfg?.backoffMaxMs ?? 15000);
    const jitterRatio = Number(cfg?.backoffJitterRatio ?? 0);

    const expRaw = base * Math.pow(Math.max(1.0, factor), Math.max(0, attempt - 1));
    const exp = Math.min(max, Math.round(expRaw));
    const jitterAmp = jitterRatio > 0 ? Math.round(exp * Math.min(0.9, Math.max(0.0, jitterRatio))) : 0;
    const jitter = (jitterAmp > 0 && jitterSeed != null && Number.isFinite(jitterSeed))
      ? deterministicJitter(Math.trunc(jitterSeed), attempt, jitterAmp)
      : 0;
    return Math.max(min, exp + jitter);
  }

  AP.Core.computeBackoffMs = computeBackoffMs;
})();
