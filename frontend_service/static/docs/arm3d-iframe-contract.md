# Arm3D iframe contract (v1)

This contract defines communication between host page (`index.html` + `arm3d-iframe-host.js`) and isolated viewer (`arm3d_viewer.html` + `arm3d-runtime.js`).

## Transport

- Channel: `window.postMessage`
- Direction: bidirectional (`host -> viewer`, `viewer -> host`)
- Expected origin: same-origin (`window.location.origin`)

## Host -> Viewer messages

### `arm3d:init`

Initial static metadata (sent on startup and on snapshot request).

```json
{
  "type": "arm3d:init",
  "payload": {
    "axis": 6,
    "type": 6,
    "mountDegrees": [0, 0],
    "endEffector": "xarm_vacuum_gripper"
  }
}
```

### `arm3d:state`

High-frequency robot state stream.

```json
{
  "type": "arm3d:state",
  "payload": {
    "joints": [0, 10.5, -22.1, 5.2, 34.6, 0.1],
    "lift": 0,
    "timestamp": 1739870000000
  }
}
```

### `arm3d:config`

Viewer runtime settings.

```json
{
  "type": "arm3d:config",
  "payload": {
    "fallbackPolling": true
  }
}
```

## Viewer -> Host messages

### `arm3d:ready`

Viewer boot completed and is ready to receive messages.

```json
{
  "type": "arm3d:ready",
  "payload": {
    "version": 1,
    "capabilities": ["init", "state", "config", "api-fallback"]
  }
}
```

### `arm3d:requestSnapshot`

Viewer requests a full snapshot (used during bootstrap/recovery).

```json
{
  "type": "arm3d:requestSnapshot",
  "payload": {
    "reason": "bootstrap"
  }
}
```

## Fallback behavior

If `postMessage` stream is unavailable, viewer polls:

- `GET /api/v1/xarm/joints_position`
- `GET /api/v1/xarm/status`

Polling can be disabled by host via `arm3d:config` with `fallbackPolling: false`.
