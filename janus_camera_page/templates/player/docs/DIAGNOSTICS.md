# Diagnostics checklist

This document is a manual checklist for debugging WebRTC/Janus connectivity and stability. It does not require code changes; use it when investigating failures or tuning.

## RTC config (client-config)

- After load, the client logs `rtc_config_loaded` with `iceTransportPolicy`, `iceServers_count`, and `server_types` (stun/turn/turns). Use this to confirm the config matches what you expect.
- If `iceTransportPolicy === 'relay'`: ensure at least one TURN (or TURNS) server is present; otherwise the client logs `rtc_config_relay_no_turn` and relay-only connectivity will fail.
- Credentials: the client logs that credentials are present (without values). For short-lived TURN credentials, ensure they are refreshed before expiry.

## WebSocket to Janus

In DevTools → Network:

- **WS upgrade**: the WebSocket to the Janus gateway (e.g. `/janus-ws`) should show a successful upgrade (101) and remain open.
- **No spurious reconnects**: there should not be repeated "101 → disconnect → reconnect" cycles without a clear reason (e.g. user stop, page navigation).
- **No mixed-content / CORS**: if the page is HTTPS, the WS URL must be WSS; avoid CORS or mixed-content errors that could break the connection.

## webrtc-internals (Chrome)

For each failed or unstable run, capture:

**A) Candidate pair (selected)**

- `localCandidateType` / `remoteCandidateType` (host, srflx, relay).
- `transport` (udp, tcp).
- `currentRoundTripTime`, `availableOutgoingBitrate` when available.

If `iceTransportPolicy` is `relay`, local candidates should be relay. If there are no relay candidates, TURN is likely unavailable or blocked by the network.

**B) Stage of failure**

- **Fails before connected:** ICE/candidates/TURN or network issue.
- **Fails after webrtcup with DTLS alert:** Often teardown/race or packets from an old session; ensure full teardown (stop → oncleanup) before a new watch.

Use Janus JS callbacks (`iceState`, `webrtcState`, `oncleanup`) to align logs with these stages.

## Test matrix (reproducibility)

Run three series of **20 starts** each (reload page or use a connect/disconnect button):

1. **Streaming only** — `?streamOnly=1` (no textroom).
2. **Textroom only** — `?textOnly=1` (no video/player).
3. **Both** — default (streaming + textroom).

Record success vs failure per run. If failures are similar across all three, the cause is likely network/TURN. If failures are much higher only when both are active, the cause is likely resource contention or races between streaming and textroom.

## Run id and event ring-buffer

- Each page load gets a unique `run_id` (in config and in log payloads). Use it to correlate console logs with a single session.
- `window.__playerEventRingBuffer.get()` returns the last N stream/handle events (type, timestamp, generation, run_id, lightweight payload). Use it after a failure to inspect the event sequence leading to the error.
