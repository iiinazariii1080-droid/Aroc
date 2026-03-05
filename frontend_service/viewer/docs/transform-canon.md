# Transform Canon (v2)

Single-source rules for arm orientation to prevent drift and duplicate rotations.

## Hierarchy

Runtime parent-child chain is strict and single-authority:

1. `world`
2. `agv` / `robot`
3. `armBase`
4. `lift`
5. `flange` / `tcp`
6. `camera` (child of `tcp` via `flangeToCamera`)

## Authority

1. `TransformAuthority` resolves world-space poses from the hierarchy above.
2. `scene-config.ts` defines static baseline only.
3. `arm:mount` is the only dynamic base-orientation input.
4. AGV pose may translate robot root; yaw is additive and must never overwrite baseline/mount.
5. On `map_id` mismatch, AGV pose must not be applied to root orientation.

## Composition Order

1. Baseline static transform (`RootTransformPolicy`)
2. Mount transform (tilt/rotation, `RootTransformPolicy`)
3. AGV transform (position + yaw policy, `AgvPosePolicy`)
4. Camera mount/debug mapping (`CameraMountPolicy`)

No other layer may directly rewrite root orientation.

## Runtime Guards

- Mount emits are deduplicated at data ingress (`robot-status`, `message-bridge`).
- Repeated unchanged mount is ignored in rendering (`arm-visual`).
- Startup in fallback gates joints/lift until mount is known.

## Release Checklist

- `transform-canon.test.ts` passes.
- `robot-status.test.ts` passes.
- Cold reload startup sequence remains: `arm:init -> arm:mount -> raw:joints/raw:lift`.
- Deployed `/arm3d_v2/assets/index-*.js` hash matches build output.
