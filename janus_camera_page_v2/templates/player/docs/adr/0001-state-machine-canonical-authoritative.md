# ADR 0001: State machine canonical as authoritative runtime

## Context

The player needs a single source of truth for state transitions and for the actions that must run when transitioning. An earlier, simpler state machine (`state_machine.js`) only computed the next state from (currentState, domainEventType). It did not produce actions or a full snapshot (generation, webrtcUp, firstFrameReceived, etc.), so the controller had to duplicate logic and risk divergence.

## Decision

- **StateMachineCanonical** (`core/state_machine_canonical.js`) is the **authoritative** runtime state machine. It takes (event, snapshot) and returns { next, actions }. PlayerController, InvariantGate, and the action executor use only this machine.
- **Legacy** (`core/state_machine_legacy.js`) is test-only: a simple (state, domainEventType) → nextState map for duality guard in tests. Runtime does not load it; only StateMachineCanonical is used for transitions and actions.

Duality is documented in both files and in [SAFETY_LAWS.md](../../SAFETY_LAWS.md). A test in `run_core_tests.js` (duality guard) asserts that for overlapping (state, event) pairs, the next state from StateMachineCanonical matches StateMachine, so the two do not drift.

## Consequences

- All transitions and snapshot updates go through one implementation; L17/L18 (fail-closed) are enforced in one place.
- New events or snapshot fields are added only in the canonical machine; the auxiliary machine can be extended for tests where a domain-event mapping exists.
- New contributors have a clear rule: runtime = StateMachineCanonical only.
