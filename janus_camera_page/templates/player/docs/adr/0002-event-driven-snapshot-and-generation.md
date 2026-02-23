# ADR 0002: Event-driven snapshot and generation

## Context

Controller state such as `firstFrameReceived` and session generation (`_sessionToken`) was originally updated in multiple places (e.g. direct assignment in frame callback or in `_fail()`). That made it hard to reason about consistency and to test: the state machine did not fully own the snapshot.

## Decision

- **firstFrameReceived** is updated only via the state machine. The controller emits `FIRST_FRAME_RECEIVED` when the first frame is observed in a valid context (CONNECTING or RECONNECTING with webrtcUp). The machine responds with a report transition setting `firstFrameReceived: true`. On recovery attempt start, the controller emits `RECOVERY_ATTEMPT_STARTED`; the machine sets `webrtcUp: false` and `firstFrameReceived: false`. The controller never assigns `_firstFrameLatch` outside constructor or `_applySnapshot`.
- **Generation (session token)** is updated only from the transition result. On FORCE_ERROR, the canonical machine’s `failClosed` bumps generation (`next.generation = snap.generation + 1`). The controller does not call a separate “bump” method; it applies `next.generation` in `_applySnapshot`. Stale-event drop continues to use the generation from the last applied snapshot.

## Consequences

- Snapshot fields are fully event-driven and auditable; tests can assert behavior by feeding events and checking the machine output.
- Single place for generation bumps on error (FORCE_ERROR) avoids double bumps and keeps the machine as source of truth for session epoch.
