# Teleop Client Specification — Joystick / Keyboard

This document defines how a client (joystick, keyboard, or custom app) connects to the nav2adapter teleop channel to drive the robot by sending velocity commands.

---

## 1. Overview

The teleop channel allows **real-time velocity control** of the AGV via HTTP. The client sends `speed` (linear, m/s) and `angular_speed` (rad/s) with a `duration` (seconds). Each request corresponds to one motion segment on the robot; the controller executes it and then stops unless the next command arrives.

Two ways to send commands:

| Option | Base URL | Use case |
|--------|----------|----------|
| **Main API** | `http://<host>:7905` | Same process as navigation/UI; requires `robot_id`. |
| **Standalone teleop server** | `http://<host>:7906` | Dedicated port, no `robot_id`; uses server-side config (SYMOVO_ROBOT_NUMBER). |

**Requirements for joystick/keyboard teleop:** the robot will only execute velocity commands when both of the following are satisfied:

1. **Drive mode is on** — `drive_mode = true` (motors enabled). Turn it on via `PUT /drive_mode?enable=true`.
2. **Charging station is deactivated** — the dock used for charging must be off (`state = INACTIVE`).  
   When you call `PUT /drive_mode?enable=true`, the backend automatically deactivates the configured charging station (if the charger workflow is enabled), so in most cases a single “enable drive mode” call is enough. You can verify with `GET /charging_stations` and ensure the station is `INACTIVE` before sending move/speed.

If the charging station stays active or drive mode is off, the controller may reject move/speed or ignore it. Always enable drive mode (and confirm charging is inactive when relevant) before starting teleop.

---

## 2. Endpoints

### 2.1 Move/speed (velocity command)

Sends one velocity command. The request is forwarded to the Symovo controller as  
`PUT https://<SYMOVO_CAR_IP>/v0/agv/<id>/move/speed` and is **blocking** until the motion for that segment finishes (or timeout).

#### Option A — Main API (recommended if you already use the same host)

- **URL:** `POST` or `PUT`  
  `http://<host>:7905/api/v1/robots/<robot_id>/move/speed`
- **Headers:** `Content-Type: application/json`
- **Body (JSON):**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `speed` | number | yes | — | Linear velocity, m/s. Positive = forward, negative = backward. |
| `angular_speed` | number | no | 0 | Angular velocity, rad/s. Positive = turn left (CCW), negative = turn right (CW). |
| `duration` | number | no | 0.25 | Duration of this command in seconds (e.g. 0.1–1.0). |

- **Success:** HTTP 200 with a JSON body (content is backend-specific; often `{"status":"ok"}` or controller response).
- **Errors:**
  - `404` — `robot_id` not found.
  - `503` — Backend or robot not ready (e.g. `MoveSpeedFailed`, `NotReady`).

**Example (curl):**

```bash
curl -s -X POST "http://localhost:7905/api/v1/robots/fahrdummy-01/move/speed" \
  -H "Content-Type: application/json" \
  -d '{"speed": 0.1, "angular_speed": 0, "duration": 0.25}'
```

#### Option B — Standalone teleop server

- **URL:** `POST` or `PUT`  
  `http://<host>:7906/move/speed`
- **Headers:** `Content-Type: application/json`
- **Body:** Same as Option A (`speed`, `angular_speed`, `duration`). Robot ID is taken from server config (`SYMOVO_ROBOT_NUMBER`).
- **Success:** HTTP 200/202, body as returned by the robot or `{"status":"ok"}`.
- **Errors:**
  - `502` — Robot returned an error.
  - `503` — Robot unreachable.

**Example (curl):**

```bash
curl -s -X PUT "http://localhost:7906/move/speed" \
  -H "Content-Type: application/json" \
  -d '{"speed": -0.1, "angular_speed": 0.5, "duration": 0.25}'
```

---

### 2.2 Drive mode (enable/disable motors)

To use the joystick or keyboard, **drive mode must be ON** (`enable=true`) and the **charging station must be deactivated**. The backend helps with the latter: calling `PUT /drive_mode?enable=true` also deactivates the configured charging station (when the charger workflow is enabled in config). So one “enable” call normally prepares the robot for teleop.

- **URL:** `PUT http://<host>:7905/drive_mode?enable=true` or `?enable=false`
- **Body:** None.
- **Success:** HTTP 200, e.g. `{"status":"ok","result":{...}}`.
- **Errors:** `503` if the Symovo controller is unavailable or rejects the command.

**Examples:**

```bash
# Enable motors (required before teleop)
curl -s -X PUT "http://localhost:7905/drive_mode?enable=true"

# Disable motors (e.g. when finishing teleop)
curl -s -X PUT "http://localhost:7905/drive_mode?enable=false"
```

---

### 2.3 Robot and readiness (optional, for UI/discovery)

- **List robots:**  
  `GET http://<host>:7905/api/v1/robots`  
  Returns `{"robots":[{"id":"fahrdummy-01","name":"Robot fahrdummy-01"}]}`. Use `id` as `robot_id` for the main API.

- **Robot status (drive_ready, state_flags):**  
  `GET http://<host>:7905/status`  
  Returns normalized AGV status; `state_flags.drive_ready` indicates whether drive mode is effectively on.

- **Charging stations:**  
  `GET http://<host>:7905/charging_stations`  
  Returns list of stations with `id`, `name`, `state` (e.g. `OK`, `INACTIVE`). Useful to confirm the dock is off before driving.

---

## 3. Protocol and units

- **speed** — Linear velocity in **m/s**. Allowed range depends on the Symovo controller; typical absolute value up to ~0.5–1.0 m/s for teleop.
- **angular_speed** — Angular velocity in **rad/s**. Positive = counter‑clockwise (turn left when viewed from above), negative = clockwise.
- **duration** — Time in **seconds** for which this velocity is applied. Common values: 0.1–0.5 s. Shorter values give more responsive but more frequent requests.

Each HTTP request corresponds to **one** motion segment. The controller runs that segment and then stops. To drive continuously, the client must send commands repeatedly while the user holds a key or moves the joystick.

---

## 4. Recommended client behaviour

### 4.1 Before starting teleop

Teleop is only possible when **drive_mode = true** and the **charging station is deactivated**. Do the following:

1. Call `PUT /drive_mode?enable=true` (on port 7905). This enables the motors and, when configured, deactivates the charging station.
2. Optionally check `GET /status` and ensure `state_flags.drive_ready === true`.
3. Optionally check `GET /charging_stations` and ensure the dock used for charging is `INACTIVE`; if it is not, velocity commands may be ignored or rejected until the station is off.

### 4.2 While driving

- Send **one** `move/speed` request per “tick” (e.g. every 150–250 ms) with the **current** desired `speed` and `angular_speed`.
- Use a fixed `duration` per request (e.g. 0.25 s). It should be on the order of your send interval so motions blend smoothly.
- When the user releases all controls, send **at least one** command with `speed: 0` and `angular_speed: 0` (and the same `duration`) to stop the robot.
- On tab/window blur or loss of focus, send the same stop command to avoid runaway.

### 4.3 Send rate and duration

- **Send interval:** 150–250 ms (about 4–7 commands per second) is a good default.
- **duration:** 0.2–0.3 s matches that rate well. Avoid `duration` much larger than the send interval to keep control responsive.
- The backend uses a timeout of about `duration + 2` seconds per request. If the controller is slow, increase `duration` or reduce send rate to avoid timeouts.

### 4.4 After teleop

- Call `PUT /drive_mode?enable=false` if you want to disable motors explicitly.

---

## 5. Keyboard mapping (typical)

| Key | Effect | Suggested body |
|-----|--------|----------------|
| W | Forward | `{"speed": 0.1, "angular_speed": 0, "duration": 0.25}` |
| S | Backward | `{"speed": -0.1, "angular_speed": 0, "duration": 0.25}` |
| A | Turn left (in place or while moving) | `{"speed": 0, "angular_speed": 0.5, "duration": 0.25}` |
| D | Turn right | `{"speed": 0, "angular_speed": -0.5, "duration": 0.25}` |
| W+A | Forward + left | `{"speed": 0.1, "angular_speed": 0.5, "duration": 0.25}` |
| W+D | Forward + right | `{"speed": 0.1, "angular_speed": -0.5, "duration": 0.25}` |
| (none) | Stop | `{"speed": 0, "angular_speed": 0, "duration": 0.25}` |

Use your chosen linear/angular limits and `duration` in the real body; the table shows the idea.

---

## 6. Joystick mapping (typical)

- **Linear axis (e.g. left stick Y or triggers):** map to `speed` in m/s, with a dead zone (e.g. ±0.05) and a scale (e.g. max ±0.3 m/s).
- **Angular axis (e.g. left stick X or right stick X):** map to `angular_speed` in rad/s, with dead zone and scale (e.g. max ±0.8 rad/s).
- **Send loop:** every 150–250 ms, read axes, apply dead zone and scaling, then send one `move/speed` with the current `speed`, `angular_speed`, and fixed `duration`.

Combined motion (e.g. forward + turn) is done by sending non‑zero `speed` and `angular_speed` in the same request.

---

## 7. Configuration (server)

Relevant environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `SERVICE_PORT` | 7905 | Main API port (robots, move/speed via robot_id, drive_mode, status, etc.). |
| `TELEOP_ENABLED` | true | Whether to start the standalone teleop server. |
| `TELEOP_HOST` | 0.0.0.0 | Bind address for the teleop server. |
| `TELEOP_PORT` | 7906 | Port for the standalone teleop server (`/move/speed`). |
| `ROBOT_ID` | — | Robot ID for the main API (e.g. `fahrdummy-01`). |
| `SYMOVO_ROBOT_NUMBER` | 15 | AGV index used on the controller; used by the teleop server and by the main API when talking to Symovo. |
| `SYMOVO_CAR_IP` | — | Controller IP for Symovo HTTP API. |

The standalone teleop server is only started if `TELEOP_ENABLED=true`. It runs in a separate thread and proxies `/move/speed` to  
`https://<SYMOVO_CAR_IP>/v0/agv/<SYMOVO_ROBOT_NUMBER>/move/speed`.

---

## 8. Errors and troubleshooting

| Symptom | Likely cause | Action |
|---------|----------------|--------|
| Joystick/keyboard has no effect; robot ignores move/speed | **drive_mode = false** or **charging station still active** | For teleop you need **drive_mode = true** and the **charging station deactivated**. Call `PUT /drive_mode?enable=true` (this also deactivates the station when configured), then check `GET /status` (`state_flags.drive_ready`) and `GET /charging_stations` (station `state` must be `INACTIVE`). |
| 404 on `/api/v1/robots/.../move/speed` | Wrong `robot_id` | Use `GET /api/v1/robots` and use the returned `id`. |
| 503 on move/speed | Drive mode off or robot not ready | Call `PUT /drive_mode?enable=true`, then check `GET /status` and `state_flags.drive_ready`. |
| 503 on move/speed | Charging connector active / safety | Ensure charging station is INACTIVE; resolve safety/errors on the controller. |
| Timeout or no motion | Controller slow or unreachable | Increase `duration`, reduce send rate, check network and `SYMOVO_CAR_IP`. |
| Robot does not stop | Client never sent stop | On “key up” or focus loss, send `{"speed":0,"angular_speed":0,"duration":0.25}`. |
| Teleop port 7906 unreachable | Teleop server off | Set `TELEOP_ENABLED=true` and ensure port 7906 is exposed (e.g. in Docker or firewall). |

---

## 9. Minimal client example (JavaScript)

```javascript
const BASE = 'http://localhost:7905';  // or teleop: 'http://localhost:7906'
const ROBOT_ID = 'fahrdummy-01';       // from GET /api/v1/robots
const DURATION = 0.25;
const SEND_MS = 180;

let keys = { w: false, s: false, a: false, d: false };
let timer = null;

async function send(speed, angular_speed) {
  const url = `${BASE}/api/v1/robots/${ROBOT_ID}/move/speed`;
  await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ speed, angular_speed, duration: DURATION }),
  });
}

function tick() {
  let s = 0, a = 0;
  if (keys.w) s += 0.1;  if (keys.s) s -= 0.1;
  if (keys.a) a += 0.5;  if (keys.d) a -= 0.5;
  send(s, a);
  if (keys.w || keys.s || keys.a || keys.d) timer = setTimeout(tick, SEND_MS);
}

document.addEventListener('keydown', (e) => {
  const k = e.key.toLowerCase();
  if (['w','s','a','d'].includes(k)) { e.preventDefault(); keys[k] = true; if (!timer) tick(); }
});
document.addEventListener('keyup', (e) => {
  const k = e.key.toLowerCase();
  if (['w','s','a','d'].includes(k)) {
    keys[k] = false;
    if (!(keys.w||keys.s||keys.a||keys.d)) { clearTimeout(timer); timer = null; send(0,0); }
  }
});
```

For the **standalone teleop server** (no `robot_id`), use  
`POST http://localhost:7906/move/speed` with the same JSON body and omit the `/api/v1/robots/...` path.

---

## 10. Reference: request/response shapes

**Move/speed request (JSON):**

```json
{
  "speed": 0.1,
  "angular_speed": 0.0,
  "duration": 0.25
}
```

**Move/speed success (example):**

```json
{ "status": "ok" }
```

Or the raw response from the Symovo controller, if the backend forwards it.

**Move/speed error (example, 503):**

```json
{
  "detail": {
    "error": { "type": "MoveSpeedFailed", "msg": "..." }
  }
}
```

**Drive mode (example):**

- Request: `PUT /drive_mode?enable=true`
- Success: `{"status":"ok","result":{...}}`

---

*Document version: 1.0. Reflects nav2adapter behaviour as of the teleop implementation (main API on 7905, standalone teleop on 7906, drive_mode and charging behaviour).*
