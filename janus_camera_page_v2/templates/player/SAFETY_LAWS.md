# SAFETY LAWS — NON-NEGOTIABLE

This document defines the hard safety laws of the Autonomous Player system.
Violation of any law MUST result in fail-closed behavior.

These laws override convenience, UX, and recovery attempts.

---

## 1. STATE OWNERSHIP

L1. The system MUST always be in exactly one explicit state.
L2. All side-effects MUST be initiated only during a state transition.
L3. No side-effects are allowed outside an active state.

---

## 2. STATE VALIDITY (INVARIANTS)

L4. PLAYING implies:
    - WebRTC is fully established
    - Exactly one active Janus session exists

L5. RECONNECTING implies:
    - No other reconnect is in progress
    - Previous sessions are fully invalidated

L6. STOPPED implies:
    - No active timers
    - No Janus sessions
    - No WebRTC tracks

L7. ERROR implies:
    - No recovery attempts
    - Only RESET is allowed

---

## 3. EVENT HANDLING

L8. Every event MUST be processed against the current state only.
L9. Events originating from previous generations MUST be ignored.
L10. Each event MUST produce a deterministic outcome.

---

## 4. RECONNECT SAFETY

L11. At most ONE reconnect may be active at any time.
L12. Reconnect attempts MUST be bounded.
L13. Reconnect failure beyond the limit MUST transition to ERROR.

---

## 5. TIME & TIMERS

L14. All timers MUST be owned by a state.
L15. Leaving a state MUST cancel all its timers.
L16. Timers MUST NOT perform side-effects directly.

---

## 6. FAIL-CLOSED POLICY

L17. Any invariant violation MUST cause immediate transition to ERROR.
L18. Unknown states or transitions MUST fail-closed.
L19. Silent recovery is forbidden.

---

## 7. IDEMPOTENCY

L20. All public commands MUST be idempotent.
L21. Duplicate commands MUST NOT cause additional side-effects.

---

## 8. OBSERVABILITY

L22. All state transitions MUST be logged.
L23. All invariant violations MUST be logged.
L24. Dropped or stale events MUST be logged.

---

End of SAFETY LAWS.

---

## Implementation notes

- **L6 STOPPED** is implemented as **IDLE** in `core/player_state.js`. The player has no separate STOPPED value; IDLE is the stopped state (no timers, no sessions).
- **L11 / L12** are enforced by `app/reconnect_coordinator.js`: at most one reconnect in progress (latch: `_inFlight` / `_scheduleTimer`); duplicate reconnect events are ignored. Reconnect start is only triggered from RECONNECTING state (START_RECONNECT_TIMER action). Attempts are bounded by `maxReconnectAttempts` (see `core/codes.js` MAX_RECONNECT_ATTEMPTS).
- **L14 / L15** Reconnect timers are owned by RECONNECTING; leaving that state (IDLE/ERROR) cancels all timers via CANCEL_ALL_TIMERS and ReconnectCoordinator.reset().
- Reconnect sequence (policy → requestRecovery → RECONNECTING → ReconnectCoordinator → success or ERROR) is described in [RECONNECT_FLOW.md](RECONNECT_FLOW.md).
- **L17 / L18** are enforced by **StateMachineCanonical** (state_machine_canonical.js): invalid transition → ERROR + LOG action. InvariantGate checks snapshot and on violation the controller transitions to ERROR. The other StateMachine (state_machine.js) is auxiliary (simple transition map); runtime uses StateMachineCanonical only.
- Key architecture decisions are documented in [docs/adr/](docs/adr/).
