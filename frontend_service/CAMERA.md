# Point Cloud Data Contract (Research, Pre-Implementation)

Status: Draft v1.1 (research + high-accuracy program)

Objective: collect and formalize all required inputs, schemas, conventions, and validation rules needed to build a high-accuracy point cloud in `arm3d_v2` before writing new code.

## Strict Phase Mode

Active phase:

- **Phase 1 (MVP) — active now**

Phase policy:

1. During Phase 1, implementation is limited to sections `1–12` and checklist tables `23.1–23.6`.
2. Sections `13–22` and checklist tables `23.7–23.8` are **Phase 2 only** and must not block MVP delivery.
3. Mixing Phase 2 requirements into Phase 1 acceptance is not allowed.

---

## 1) Scope and Accuracy Target

This document defines the **data truth model** for point-cloud construction in `arm3d_v2`:

- Input domains: depth/RGB, robot kinematics, mount/TCP, AGV/map, timestamps.
- Transform authority: canonical chain from `viewer/docs/transform-canon.md`.
- Output domain: world-frame point cloud suitable for stable multi-frame accumulation.

MVP target:

- Stable cloud placement in AGV/map world frame.
- No hidden fallback transform sources.
- Deterministic behavior with HTTP polling transport.

---

## 2) Canonical Camera Definition (v2 Source of Truth)

The historical camera snippet is retained for context, but v2 values must come from `viewer/src/config/scene-config.ts`.

Legacy snippet (reference only):

```ts
export const DEPTH_CAMERA = {
  model: "RealSense D435",
  intrinsics: { fx: 380.425, fy: 380.425, cx: 232.374, cy: 324.825, w: 480, h: 640 },
  depthScale: 0.9,
  depthCalibration: { k: 0.957, b: 45.2 },
  fov: { h: 64.8, v: 80.2 },
  frustum: { near: 0.1, far: 1.0 },
  mounting: "eye-in-hand",
  endpoint: "http://192.168.1.55:8000",
};
```

Canonical v2 parameters (must be synchronized in code and docs):

- Intrinsics: `fx/fy/cx/cy/width/height`
- Depth conversion: `depthScale`, `depthCalibration.k`, `depthCalibration.b`
- Extrinsics: `flangeToCamera.translation + rotation`
- Overlay preprocessing: `stridePx`, `minRaw`, `maxRaw`, `minDistanceM`, `rotatePixel`, `flipX/flipY`, `cloudMirrorVertical`

---

## 3) Data Sources and Paths

### 3.1 Depth/RGB Frame Source (MVP transport: HTTP polling)

- Endpoint: `/api/v1/depth_camera/depth/frame_color_overlay?format=json`
- Consumer: `viewer/src/data/depth-api.ts`
- Expected payload aliases:
  - Depth: `depth_data | depth | data`
  - RGB: `mapped_color | rgb_data | color`
  - Dimensions: `width`, `height`

### 3.2 Robot Status Source

- Endpoint: `/api/v1/robot/status`
- Consumer: `viewer/src/data/robot-status.ts`
- Parsed fields include:
  - mount degrees (tilt/rotation)
  - joints/raw joints
  - lift/raw lift
  - AGV pose (`x_m`, `y_m`, `theta_deg`, `map_id`)

### 3.3 Host Messaging Source (postMessage)

- Message bridge: `viewer/src/data/message-bridge.ts`
- Input events: `arm3d:bootstrap`, `arm3d:init`, `arm3d:state`, `arm3d:config`
- Contract reference: `static/docs/arm3d-iframe-contract.md`

### 3.4 AGV/Map Source

- Configured endpoints in `viewer/src/config/scene-config.ts`
- Render path: `viewer/src/rendering/agv-map-visual.ts`

---

## 4) Required Inputs Checklist (for accurate cloud)

## Must-Have

1. Depth frame (raw u16) + valid `width/height`.
2. Intrinsics (`fx`, `fy`, `cx`, `cy`, dimensions).
3. Depth conversion (`depthScale`, `k`, `b`).
4. Camera mount extrinsics (`flangeToCamera`).
5. Canonical chain state for camera world pose (TCP-driven camera in transform canon).
6. Timestamp pair (frame timestamp + robot-status timestamp at ingestion time).
7. AGV pose and `map_id` validity if world frame is AGV/map-based.

## Optional (high value)

1. RGB aligned to depth for colored cloud.
2. Confidence/quality mask per pixel.
3. Explicit upstream capture timestamp from robot and camera services.

---

## 5) Payload Contracts (Normalization Rules)

## 5.1 Depth/RGB Frame Contract

- Accept alias fields, normalize into one internal shape:
  - `depth_u16: Uint16Array`
  - `rgb_u8: Uint8Array | null`
  - `width: number`, `height: number`
  - `timestamp_ms: number`
- Units:
  - raw depth in sensor-native units before calibration
  - converted depth in **meters** for unprojection

## 5.2 Robot Status Contract

- Normalize mount/joints/lift/agv into one status object with timestamp.
- Units:
  - joints: degrees
  - lift: meters (or explicit conversion documented)
  - AGV: `x_m`, `y_m` in meters, `theta_deg` in degrees
  - mount: degrees

## 5.3 Message Contract

- Every message event must include explicit `type`.
- If timestamp is missing, fallback stamp must be marked as non-source timestamp.

---

## 6) Coordinate and Transform Canon

Single authority (no duplicates):

1. `world`
2. `agv` / `robot`
3. `armBase`
4. `lift`
5. `flange` / `tcp`
6. `camera` (child of tcp/flange via `flangeToCamera`)

Point transform model:

1. pixel/depth → camera-local point (`Xc`, `Yc`, `Zc`)
2. camera-local → world via canonical camera world matrix

Hard rule:

- `flangeToCamera` must be applied exactly once.
- No second transform authority in renderer/domain fallback paths.

---

## 7) Preprocessing and Filtering Model

MVP preprocessing stages:

1. Depth range filter: `minRaw <= raw <= maxRaw`
2. Distance filter: `Z >= minDistanceM`
3. Optional color validity filter: reject near-black RGB
4. Pixel orientation normalization: rotation/flip/mirror
5. Stride subsampling for performance

All filter thresholds must be declared in one canonical config block.

---

## 8) Time and Sync Model

Accuracy-critical requirement:

- Point frame must be paired with robot pose from near-identical time window.

Define and enforce:

1. `frame_timestamp_ms`
2. `pose_timestamp_ms`
3. `skew_ms = |frame - pose|`
4. acceptance threshold for skew

If skew exceeds threshold:

- mark frame as low-confidence or drop from accumulation.

---

## 9) Known Risks and Gaps (from current codebase)

1. Parameter drift between files (`CAMERA.md`, legacy static config, v2 config).
2. Possible transform duplication if camera pose is derived in more than one layer.
3. Legacy/v2 coordinate mapping differences.
4. Timestamp fallback to local `Date.now()` can hide transport jitter.
5. `map_id` handling is not fully uniform across all paths.
6. DMP1 format version mismatch risk (v1/v2 writer-reader compatibility).

---

## 10) Validation Rules and Example Checks

Before coding new cloud renderer path, verify:

1. Intrinsics fields exist and are finite.
2. `depthScale`, `k`, `b` are finite and within expected range.
3. Frame dimensions match payload lengths.
4. Canonical camera world matrix available for each processed frame.
5. `map_id` policy enforced for AGV world application.
6. Timestamp skew is measured and logged.

---

## 11) Textual Model v1 (No Code)

Pipeline:

1. **Ingest** depth/RGB frame + status + timestamp.
2. **Normalize** payload aliases into canonical typed frame object.
3. **Calibrate depth** with scale and linear calibration (`true_mm = k*raw + b`).
4. **Unproject** to camera-local points using intrinsics.
5. **Filter** invalid points by raw/depth/color/stride policy.
6. **Transform** points using canonical camera world matrix from chain authority.
7. **Render/Accumulate** in AGV/map world frame.
8. **Validate** skew/map_id/transform sanity for each frame.

Invariants:

- one transform authority,
- one parameter source of truth,
- explicit units,
- explicit timestamp quality.

---

## 12) Operational Checklist Before Implementation

1. Confirm canonical values in `viewer/src/config/scene-config.ts`.
2. Mark this file as documentation-only and align terminology with transform canon.
3. Confirm endpoint payload examples from live responses.
4. Confirm map_id policy in orchestrator + renderer paths.
5. Freeze MVP scope (HTTP polling, no auto-calibration/ICP/full optimization).

When all checklist items are complete, implementation can start with minimal, maintainable point-cloud code in `arm3d_v2`.

---

## 13) High-Accuracy Profile (Target)

This section defines stricter quality gates beyond MVP.

Profile modes:

- `mvp`: stability-first, moderate filtering, no compensation.
- `high_accuracy`: strict sync, strict metrics, regression-gated release.

For `high_accuracy`, all sections 14–20 are mandatory.

### Out of Scope (MVP Now)

The following are explicitly out of scope while Phase 1 is active:

1. Benchmark-gated RMSE/MAE release blocking.
2. Motion compensation requirements from section 19.
3. Distortion-model expansion beyond current stream assumptions.
4. Regression release gating from section 20.
5. High-accuracy observability counters as delivery blockers.

These items remain documented and are activated only in Phase 2.

---

## 14) Quality Metrics and Pass/Fail Thresholds

Core metrics (world-frame, millimeters unless noted):

1. **Static RMSE** (same static scene, repeated captures)
2. **Static MAE**
3. **Frame-to-frame centroid drift**
4. **95th percentile nearest-neighbor residual**
5. **Dropped frame ratio** (sync/filter rejects)
6. **Timestamp skew p95** (milliseconds)

Initial acceptance thresholds (provisional, to refine with real data):

- Static RMSE: <= 12 mm
- Static MAE: <= 8 mm
- Centroid drift (10 consecutive frames): <= 10 mm
- NN residual p95: <= 20 mm
- Dropped frame ratio: <= 5%
- Timestamp skew p95: <= 40 ms

If any metric fails, build/release is `NO-GO`.

---

## 15) Error Budget (Source Contribution Limits)

Maximum contribution per subsystem (target allocation):

1. Depth noise + calibration residual: <= 6 mm
2. Intrinsics uncertainty: <= 3 mm
3. Extrinsics (`flangeToCamera`) uncertainty: <= 6 mm
4. Pose/depth time skew impact: <= 5 mm
5. AGV/map/world anchoring: <= 4 mm

Budget rule:

- Total expected error = RSS of components (root-sum-square), not simple sum.
- If one component exceeds budget, compensation in other components is not accepted as permanent fix.

---

## 16) Time Sync SLA and Pose Pairing

Required timestamps:

1. `depth_capture_ts_ms` (source timestamp preferred)
2. `robot_pose_ts_ms` (source timestamp preferred)
3. `ingest_ts_ms` (local fallback, diagnostic only)

Pairing policy:

1. Select nearest robot pose to depth timestamp.
2. If two bracketing poses exist, interpolate pose at depth timestamp.
3. If `skew_ms > skew_hard_limit_ms`, drop frame.

Provisional limits:

- `skew_soft_limit_ms = 40`
- `skew_hard_limit_ms = 80`

Diagnostics must log skew histogram and dropped-frame reasons.

---

## 17) Camera Geometry and Distortion Policy

Before high-accuracy mode is enabled, one of the following must be true:

1. Stream is already rectified (distortion compensated upstream), or
2. Distortion correction is applied explicitly before unprojection.

If distortion is unknown:

- System remains in `mvp` profile,
- high-accuracy metrics are informative only (not release-gating).

Distortion metadata fields (if available) should be added to contract:

- `k1, k2, p1, p2, k3` + distortion model type.

---

## 18) Calibration Lifecycle (`flangeToCamera`)

Lifecycle states:

1. `known-good` (validated parameters)
2. `suspect` (metrics drifting)
3. `recalibration-required`

Triggers for recalibration:

- RMSE threshold violation on benchmark dataset,
- hardware remount/impact,
- persistent drift increase > 25% week-over-week.

Post-calibration acceptance:

1. Benchmark pass on static + dynamic scenes.
2. No regression in time sync or dropped-frame ratio.
3. Parameter update recorded with date, dataset version, and metric deltas.

---

## 19) Motion Compensation Policy

High-accuracy requirements during motion:

1. If robot/TCP angular or linear velocity exceeds configured limit, either:
  - compensate using interpolated pose trajectory, or
  - mark frame as non-accumulative.
2. Do not accumulate unsynchronized moving frames into persistent map.

Provisional gating flags per frame:

- `pose_interp_used: boolean`
- `motion_gate_reject: boolean`
- `accumulation_allowed: boolean`

---

## 20) Benchmark Dataset and Regression Protocol

Benchmark set must contain:

1. Static near scene
2. Static mid/far scene
3. Fine-geometry scene (edges/corners)
4. Slow-motion robot sequence
5. AGV pose/map transition scenario (with map_id checks)

For each benchmark run, store:

- config snapshot (intrinsics/extrinsics/filters),
- metric report (RMSE/MAE/drift/skew/drop-rate),
- pass/fail verdict.

Release gate:

- `high_accuracy` can ship only if benchmark suite passes all thresholds in section 14.

---

## 21) Observability and Runtime Diagnostics

Mandatory runtime diagnostics:

1. Current profile (`mvp` or `high_accuracy`)
2. Camera parameter version hash
3. Transform authority source ID
4. Timestamp skew stats (rolling p50/p95/p99)
5. Frame reject counters by reason
6. map_id mismatch counters

No silent fallback allowed in high-accuracy mode.

---

## 22) High-Accuracy Readiness Checklist

System is high-accuracy ready only when all are true:

1. Sections 14–21 implemented and measured on benchmark dataset.
2. Canonical camera parameters are versioned and reproducible.
3. Pose-depth pairing uses source timestamps or validated interpolation.
4. Distortion policy is explicit (`rectified` or `corrected`).
5. Transform chain has single authority and no double-application paths.
6. Release pipeline enforces pass/fail automatically.

---

## 23) Implementation Checklist Tables

Format:

- **Field**: canonical field name used internally.
- **Unit**: expected unit/encoding.
- **Source**: primary source path (endpoint/event/config).
- **Validator**: concrete runtime validation rule.
- **Threshold**: pass/fail limit (or `N/A` if structural).

### 23.1 Depth/RGB Frame Ingest

| Field | Unit | Source | Validator | Threshold |
|---|---|---|---|---|
| `depth_u16` | uint16 array | `/api/v1/depth_camera/depth/frame_color_overlay?format=json` (`depth_data\|depth\|data`) | length == `width*height` | Required |
| `rgb_u8` | uint8 array (optional) | same payload (`mapped_color\|rgb_data\|color`) | if present, length == `width*height*3` | Optional |
| `width` | pixels | depth payload | integer, `>0` | Required |
| `height` | pixels | depth payload | integer, `>0` | Required |
| `depth_capture_ts_ms` | ms epoch | sensor payload preferred | finite integer | skew policy applies |
| `ingest_ts_ms` | ms epoch | local receiver time | finite integer | diagnostic only |

### 23.2 Camera Model and Calibration

| Field | Unit | Source | Validator | Threshold |
|---|---|---|---|---|
| `fx` | px | `viewer/src/config/scene-config.ts` | finite, `>0` | Required |
| `fy` | px | `viewer/src/config/scene-config.ts` | finite, `>0` | Required |
| `cx` | px | `viewer/src/config/scene-config.ts` | finite, `0<=cx<width` | Required |
| `cy` | px | `viewer/src/config/scene-config.ts` | finite, `0<=cy<height` | Required |
| `depthScale` | scalar | `viewer/src/config/scene-config.ts` | finite | `0.5..1.5` |
| `depthCalibration.k` | scalar | `viewer/src/config/scene-config.ts` | finite | `0.8..1.1` |
| `depthCalibration.b` | mm | `viewer/src/config/scene-config.ts` | finite | `-200..200` |
| `flangeToCamera.translation` | m | `viewer/src/config/scene-config.ts` | finite XYZ | `|component| <= 0.5` |
| `flangeToCamera.rotation` | rad | `viewer/src/config/scene-config.ts` | finite RPY | `|component| <= π` |

### 23.3 Robot/AGV Pose State

| Field | Unit | Source | Validator | Threshold |
|---|---|---|---|---|
| `robot_pose_ts_ms` | ms epoch | `/api/v1/robot/status` or message bridge | finite integer | skew policy applies |
| `joints[6]` | deg | robot status / bridge | length==6, finite | required for chain update |
| `lift` | m | robot status / bridge | finite | project-specific safe range |
| `mount.tilt_deg` | deg | robot status (`xarm.data[51]`) | finite | `|tilt| <= 180` |
| `mount.rotation_deg` | deg | robot status (`xarm.data[51]`) | finite | `|rotation| <= 360` |
| `agv.x_m` | m | status/AGV source | finite | required for AGV world |
| `agv.y_m` | m | status/AGV source | finite | required for AGV world |
| `agv.theta_deg` | deg | status/AGV source | finite | wrapped to `[-180,180]` |
| `agv.map_id` | id/string | status/AGV source | non-empty if AGV world enabled | mismatch => reject AGV apply |

### 23.4 Transform and Authority

| Field | Unit | Source | Validator | Threshold |
|---|---|---|---|---|
| `camera_world_matrix` | 4x4 matrix | canonical chain (`transform-canon`) | shape 4x4, finite | Required |
| `authority_source` | enum | runtime transform layer | must be single source | no dual authority |
| `flangeToCamera_apply_count` | count | runtime diagnostic | equals 1 | hard fail otherwise |
| `world_frame_id` | string | orchestrator/render config | equals active AGV/map frame id | Required |

### 23.5 Preprocessing / Filtering

| Field | Unit | Source | Validator | Threshold |
|---|---|---|---|---|
| `stridePx` | px step | scene config | integer `>=1` | default 4 |
| `minRaw` | raw depth units | scene config | finite integer | default 50 |
| `maxRaw` | raw depth units | scene config | finite integer | default 2000 |
| `minDistanceM` | m | scene config | finite, `>0` | default 0.10 |
| `nearBlackThreshold` | 0..255 | processing policy | integer | default 12 |
| `rotatePixel` | deg | processing policy | enum `{0,90,180,270}` | profile-defined |
| `flipX/flipY` | bool | processing policy | boolean | profile-defined |
| `cloudMirrorVertical` | bool | processing policy | boolean | profile-defined |

### 23.6 Time Sync and Pairing

| Field | Unit | Source | Validator | Threshold |
|---|---|---|---|---|
| `skew_ms` | ms | `abs(depth_capture_ts_ms - robot_pose_ts_ms)` | finite, `>=0` | soft `<=40`, hard `<=80` |
| `pairing_mode` | enum | sync module | one of `{nearest,interpolated}` | Required |
| `pose_interp_used` | bool | sync module | boolean | diagnostic |
| `frame_accept` | bool | sync gate | false when hard limit exceeded | hard gate |
| `drop_reason` | enum | sync/filter gates | non-empty when dropped | Required for observability |

### 23.7 Quality Metrics (Release Gates)

| Field | Unit | Source | Validator | Threshold |
|---|---|---|---|---|
| `static_rmse_mm` | mm | benchmark report | finite | `<=12` |
| `static_mae_mm` | mm | benchmark report | finite | `<=8` |
| `centroid_drift_10f_mm` | mm | benchmark report | finite | `<=10` |
| `nn_residual_p95_mm` | mm | benchmark report | finite | `<=20` |
| `drop_ratio_pct` | % | runtime/benchmark | finite | `<=5` |
| `skew_p95_ms` | ms | runtime/benchmark | finite | `<=40` |

### 23.8 Observability Counters

| Field | Unit | Source | Validator | Threshold |
|---|---|---|---|---|
| `profile_mode` | enum | runtime config | one of `{mvp,high_accuracy}` | Required |
| `camera_param_version` | hash/string | config snapshot | non-empty | Required |
| `map_id_mismatch_count` | count | AGV/map guard | monotonic integer | investigate if >0 |
| `motion_gate_reject_count` | count | motion compensation gate | monotonic integer | investigate trend |
| `sync_hard_drop_count` | count | sync gate | monotonic integer | investigate trend |

---

## 24) Execution Order (Checklist Use)

Use tables in this order during implementation.

### Phase 1 (MVP) — active now

Required order:

1. `23.1` + `23.2` (frame + camera model)
2. `23.3` + `23.4` (pose + transform authority)
3. `23.5` + `23.6` (preprocessing + sync gating)

Phase 1 completion gate:

- all **Required** validators in `23.1–23.6` pass,
- no dependency on sections `13–22` for MVP sign-off.

### Phase 2 (High-Accuracy) — after MVP freeze

Activate only after Phase 1 is marked complete.

Required order:

4. `23.7` + `23.8` (quality gates + observability)

Phase 2 completion gate:

- all **Required** validators pass,
- section `23.7` thresholds are met on benchmark dataset,
- sections `14–22` are operationally enforced.
