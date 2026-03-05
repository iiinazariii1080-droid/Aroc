# Lift Integration Guide (HTTP + MQTT)

Audience: frontend developers integrating the IGUS Dryve D1 lift.

This guide explains how to control the lift through:
- **MQTT via backend bridge** (recommended primary flow)
- **HTTP v1 API** (target contract and fallback diagnostics)

Recommended reading order for frontend teams:
1. Sections **3-5** (what to send and what you receive)
2. Sections **6-7** (ready-to-run command examples and JS integration)
3. Sections **9-12.1** (error handling, reliability, state machine)

---

## 1) Scope and conventions

- **Primary transport**: MQTT command/response topics via `mqtt_command_service` bridge.
- **Lift API contract**: **IGUS v1 only**, paths under `/drive/*`.
- **Legacy paths** like `/move`, `/status`, `/reference` are intentionally not used in examples.

Identifiers used in this guide:
- `robot_id`: robot namespace in MQTT topics
- `request_id`: unique correlation ID per frontend action
- `command_id`: optional logical command ID; if omitted in some bridge flows, bridge may fallback to `request_id`

---

## 2) End-to-end architecture

```text
Frontend UI
  ├─ publish command JSON -> aroc/robot/{robot_id}/cmd/igus
  └─ subscribe responses   <- aroc/robot/{robot_id}/resp/igus

mqtt_command_service bridge
  └─ calls IGUS HTTP API (v1 /drive/*)

igus_service (Dryve D1)
```

Practical meaning:
1. Frontend sends MQTT command envelope with `method`, `path`, `body`.
2. Bridge executes HTTP call against IGUS service.
3. Bridge publishes ACK/result/error on response topic.
4. Frontend matches by `request_id`.

---

## 3) Prerequisites

You need:
- MQTT broker host/port/user/password
- `robot_id`
- MQTT client in frontend runtime (usually WebSocket MQTT client)
- If bridge endpoint requires auth to upstream HTTP: pass API key in command envelope `headers`.
- For terminal testing: `mosquitto_pub` and `mosquitto_sub`.

Known working integration values (current environment):
- MQTT broker: `82.165.177.194:8883` (TLS)
- MQTT username: `bridge_user`
- MQTT password: `G456AH37gbc`
- Robot ID: `fahrdummy-01`

Typical topic names:
- Command topic: `aroc/robot/{robot_id}/cmd/igus`
- Response topic: `aroc/robot/{robot_id}/resp/igus`

---

## 4) MQTT command envelope (frontend -> bridge)

Use this generic shape for all lift operations:

```json
{
  "request_id": "req-20260303-0001",
  "method": "POST",
  "path": "/drive/move_to_position",
  "headers": {
    "x-api-key": "<optional-upstream-api-key>",
    "content-type": "application/json"
  },
  "body": {
    "target_position": 25000,
    "relative": false,
    "profile": {
      "velocity": 5000,
      "acceleration": 2500,
      "deceleration": 2500
    },
    "timeout_ms": 30000
  },
  "timeout": 35
}
```

Notes:
- `path` must be an IGUS v1 path (for example `/drive/status`, `/drive/jog_start`).
- `timeout` is bridge-side HTTP timeout in seconds.
- Always generate a unique `request_id`.

---

## 5) MQTT response shape (bridge -> frontend)

Bridge responses are flat ACK objects. Frontend should parse top-level fields first:
- `type`
- `request_id`
- `service`
- `success`
- `status_code`
- `body`
- `error`

### 5.1 Success ACK example

```json
{
  "type": "ack",
  "request_id": "req-20260303-0001",
  "service": "igus",
  "success": true,
  "status_code": 200,
  "headers": {
    "content-type": "application/json",
    "x-request-id": "6f8c4a..."
  },
  "body": {
    "ok": true,
    "data": {
      "accepted": true,
      "state": "moving"
    },
    "error": null,
    "meta": {
      "request_id": "6f8c4a...",
      "command_id": "cmd-81fd...",
      "ts": "2026-03-03T12:00:05Z"
    }
  },
  "error": null,
  "timestamp": "2026-03-03T12:00:05.102Z"
}
```

### 5.2 Upstream HTTP error example

```json
{
  "type": "ack",
  "request_id": "req-20260303-0002",
  "service": "igus",
  "success": false,
  "status_code": 422,
  "body": {
    "detail": "Invalid request payload"
  },
  "error": {
    "type": "http_error",
    "message": "HTTP 422"
  },
  "timestamp": "2026-03-03T12:00:08.448Z"
}
```

### 5.3 Transport/network failure example

```json
{
  "type": "ack",
  "request_id": "req-20260303-0003",
  "service": "igus",
  "success": false,
  "status_code": 0,
  "body": null,
  "error": {
    "type": "http_error",
    "message": "Connection failed"
  },
  "timestamp": "2026-03-03T12:00:10.912Z"
}
```

Important parsing rules:
- If `status_code === 0`, treat as transport/bridge failure, not IGUS business error.
- Prefer `request_id` for correlation.
- Use top-level `error` as the primary failure source.
- Read IGUS/domain payload from `body`.

---

## 6) HTTP v1 endpoint cookbook (target lift contract)

Base URLs:
- Direct: `http://<host>:8101`
- Via API Gateway: `http://<gateway>:8201/api/v1/igus`

All examples below use v1 endpoints.

### 6.1 Read status

**HTTP**
```http
GET /drive/status
```

**MQTT command envelope**
```json
{
  "request_id": "req-status-1",
  "method": "GET",
  "path": "/drive/status",
  "timeout": 10
}
```

### 6.2 Move to absolute position

**HTTP**
```http
POST /drive/move_to_position
Content-Type: application/json
```

```json
{
  "target_position": 15000,
  "relative": false,
  "profile": {
    "velocity": 4000,
    "acceleration": 2000,
    "deceleration": 2000
  },
  "timeout_ms": 30000
}
```

**MQTT envelope**
```json
{
  "request_id": "req-move-1",
  "method": "POST",
  "path": "/drive/move_to_position",
  "body": {
    "target_position": 15000,
    "relative": false,
    "profile": {
      "velocity": 4000,
      "acceleration": 2000,
      "deceleration": 2000
    },
    "timeout_ms": 30000
  },
  "timeout": 35
}
```

### 6.2.1 Command completion semantics

For `POST /drive/move_to_position`, a successful ACK means the command was accepted.
Do not treat that ACK as motion finished. Confirm completion by polling `/drive/status` until:
- `status_bits.target_reached = true`
- `velocity = 0`

If a `504` appears from an upstream gateway, treat the command state as **indeterminate** and reconcile with `/drive/status` before retrying.

### 6.3 Jog start/update/stop (hold-to-move)

Jog requires periodic keepalive while button is pressed.

#### Start jog
```json
{
  "request_id": "req-jog-start-1",
  "method": "POST",
  "path": "/drive/jog_start",
  "body": {
    "direction": "positive",
    "speed": 2000,
    "ttl_ms": 1000
  },
  "timeout": 10
}
```

#### Keepalive update (repeat periodically, for example every 100-300 ms)
```json
{
  "request_id": "req-jog-update-1",
  "method": "POST",
  "path": "/drive/jog_update",
  "body": {
    "direction": "positive",
    "speed": 2000,
    "ttl_ms": 1000
  },
  "timeout": 10
}
```

#### Stop jog
```json
{
  "request_id": "req-jog-stop-1",
  "method": "POST",
  "path": "/drive/jog_stop",
  "body": {},
  "timeout": 10
}
```

### 6.4 Emergency/quick stop

```json
{
  "request_id": "req-stop-1",
  "method": "POST",
  "path": "/drive/stop",
  "body": {
    "mode": "quick_stop",
    "timeout_ms": 5000
  },
  "timeout": 10
}
```

### 6.5 Reference (homing)

```json
{
  "request_id": "req-ref-1",
  "method": "POST",
  "path": "/drive/reference",
  "body": {},
  "timeout": 60
}
```

### 6.6 Fault reset

```json
{
  "request_id": "req-fault-reset-1",
  "method": "POST",
  "path": "/drive/fault_reset",
  "body": {},
  "timeout": 10
}
```

### 6.7 MQTT CLI examples (same style as main README)

Use two terminals:
1. Terminal A: subscribe to responses.
2. Terminal B: publish commands.

#### A) Subscribe to responses

```bash
# TLS (8883)
mosquitto_sub -h 82.165.177.194 -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'G456AH37gbc' \
  -t "aroc/robot/fahrdummy-01/resp/igus" -v
```

#### B) Read lift status (`/drive/status`)

```bash
mosquitto_pub -h 82.165.177.194 -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'G456AH37gbc' \
  -t "aroc/robot/fahrdummy-01/cmd/igus" \
  -m '{
    "request_id": "status-001",
    "method": "GET",
    "path": "/drive/status",
    "timeout": 10
  }'
```

#### C) Move to position (`/drive/move_to_position`)

```bash
mosquitto_pub -h 82.165.177.194 -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'G456AH37gbc' \
  -t "aroc/robot/fahrdummy-01/cmd/igus" \
  -m '{
    "request_id": "move-001",
    "method": "POST",
    "path": "/drive/move_to_position",
    "body": {
      "target_position": 30000,
      "relative": false,
      "profile": {
        "velocity": 4000,
        "acceleration": 2000,
        "deceleration": 2000
      },
      "timeout_ms": 30000
    },
    "timeout": 35
  }'
```

#### D) Jog start / update / stop

```bash
# jog_start
mosquitto_pub -h 82.165.177.194 -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'G456AH37gbc' \
  -t "aroc/robot/fahrdummy-01/cmd/igus" \
  -m '{
    "request_id": "jog-start-001",
    "method": "POST",
    "path": "/drive/jog_start",
    "body": {"direction": "positive", "speed": 2000, "ttl_ms": 1000},
    "timeout": 10
  }'

# jog_update (repeat while button is held)
mosquitto_pub -h 82.165.177.194 -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'G456AH37gbc' \
  -t "aroc/robot/fahrdummy-01/cmd/igus" \
  -m '{
    "request_id": "jog-update-001",
    "method": "POST",
    "path": "/drive/jog_update",
    "body": {"direction": "positive", "speed": 2000, "ttl_ms": 1000},
    "timeout": 10
  }'

# jog_stop
mosquitto_pub -h 82.165.177.194 -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'G456AH37gbc' \
  -t "aroc/robot/fahrdummy-01/cmd/igus" \
  -m '{
    "request_id": "jog-stop-001",
    "method": "POST",
    "path": "/drive/jog_stop",
    "body": {},
    "timeout": 10
  }'
```

#### E) Quick stop (`/drive/stop`)

```bash
mosquitto_pub -h 82.165.177.194 -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'G456AH37gbc' \
  -t "aroc/robot/fahrdummy-01/cmd/igus" \
  -m '{
    "request_id": "stop-001",
    "method": "POST",
    "path": "/drive/stop",
    "body": {"mode": "quick_stop", "timeout_ms": 5000},
    "timeout": 10
  }'
```

Tip: for jog, keep sending `jog_update` every 100-300 ms with `ttl_ms: 1000`.

---

## 7) Frontend JavaScript integration pattern

```js
import mqtt from 'mqtt';

const robotId = 'fahrdummy-01';
const cmdTopic = `aroc/robot/${robotId}/cmd/igus`;
const respTopic = `aroc/robot/${robotId}/resp/igus`;

// For browser apps, use broker WebSocket endpoint (wss://...:<ws-port>),
// not raw MQTT TLS port 8883 unless your broker maps it to WebSockets.
const client = mqtt.connect('wss://mqtt.example.com:8083', {
  username: 'bridge_user',
  password: 'G456AH37gbc',
  reconnectPeriod: 2000
});

const pending = new Map();

client.on('connect', () => {
  client.subscribe(respTopic);
});

client.on('message', (topic, payload) => {
  if (topic !== respTopic) return;

  let msg;
  try {
    msg = JSON.parse(payload.toString('utf-8'));
  } catch {
    return;
  }

  const requestId = msg.request_id;
  if (!requestId || !pending.has(requestId)) return;

  const { resolve, reject, timer } = pending.get(requestId);
  clearTimeout(timer);
  pending.delete(requestId);

  if (msg.status_code === 0) {
    reject(new Error(msg.error?.message || 'Bridge transport error'));
    return;
  }

  if (msg.error || !msg.success || (msg.status_code || 500) >= 400) {
    const details = msg.error?.message || msg.body?.error?.message || msg.body?.detail || `HTTP ${msg.status_code}`;
    reject(new Error(details));
    return;
  }

  resolve(msg.body);
});

function sendLiftCommand({ method, path, body, timeout = 15, headers = {} }) {
  const requestId = crypto.randomUUID();

  const envelope = {
    request_id: requestId,
    method,
    path,
    timeout,
    headers: {
      'content-type': 'application/json',
      ...headers
    },
    ...(body !== undefined ? { body } : {})
  };

  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pending.delete(requestId);
      reject(new Error('Client timeout waiting for MQTT response'));
    }, (timeout + 2) * 1000);

    pending.set(requestId, { resolve, reject, timer });
    client.publish(cmdTopic, JSON.stringify(envelope), { qos: 1 });
  });
}

// Example usage
await sendLiftCommand({ method: 'GET', path: '/drive/status', timeout: 10 });
await sendLiftCommand({
  method: 'POST',
  path: '/drive/move_to_position',
  body: {
    target_position: 18000,
    relative: false,
    profile: { velocity: 4000, acceleration: 2000, deceleration: 2000 },
    timeout_ms: 30000
  },
  timeout: 35
});
```

---

## 8) Correlation and tracing strategy

Use these fields consistently:
- Frontend-generated `request_id`: primary request/response correlation in MQTT.
- Bridge/IGUS `x-request-id` header (if present): useful for backend logs.
- IGUS response `meta.command_id`: useful for operation tracking in drive internals.

Recommended practice:
- Log `{ request_id, topic, path, status_code, x-request-id, command_id }` for every operation.
- For difficult cases, query IGUS trace endpoint `/drive/trace/latest` via the same MQTT proxy pattern.

---

## 9) Error handling model

Handle errors in 4 layers:

1. **Transport layer** (MQTT/bridge):
   - `status_code: 0` or no response before client timer.
   - Action: reconnect/retry with backoff.

2. **HTTP layer** (upstream IGUS):
   - `status_code >= 400`.
   - Action: parse top-level `error` first, then `body` details.

3. **Indeterminate timeout layer** (gateway `504`):
   - `status_code = 504` can mean timeout on proxy path while command may still execute.
   - Action: do not immediately re-send motion command; poll `/drive/status` and reconcile state first.

4. **Domain layer** (IGUS business state):
   - `body.ok = false` or domain error codes (validation, motion conflict, service unavailable).
   - Action: show actionable UI feedback (fault reset, retry, check homing, etc.).

Suggested user-visible categories:
- `Connectivity problem`
- `Invalid command`
- `Drive busy or conflict`
- `Safety/fault state`

---

## 10) Timeouts, retries, and idempotency

### Timeout layers
- Frontend promise timeout (must be slightly larger than envelope `timeout`).
- Bridge HTTP timeout (`timeout` field in envelope).
- IGUS operation timeout in body (`timeout_ms` for move/stop where applicable).

### Retry policy
- Safe to retry **read** commands (`GET /drive/status`).
- For motion commands, retry only with strong correlation logic and user awareness.
- Use exponential backoff for transient connectivity failures.

### Idempotency/dedup notes
Bridge includes dedup behavior (in-flight duplicate suppression and cached replay for recent completed commands). Frontend should still:
- Never intentionally send duplicate motion command bursts.
- Keep one active command per UI action.
- Disable action buttons while command is pending.

---

## 11) Jog UX checklist (important)

When implementing hold-to-jog button:
- On press: send `/drive/jog_start`.
- While pressed: send `/drive/jog_update` heartbeat faster than `ttl_ms` expiration.
- On release/cancel/blur/unmount: always send `/drive/jog_stop`.
- On keepalive error: stop heartbeat, mark jog inactive, send best-effort `/drive/jog_stop`.

Detailed transition logic is defined in **12.1 Frontend state machine**.

---

## 12) Production readiness checklist

- [ ] All paths are v1 `/drive/*` only
- [ ] Unique `request_id` for every publish
- [ ] Frontend timeout > bridge timeout
- [ ] Proper parsing for `status_code: 0` transport errors
- [ ] Button lock/debounce to prevent duplicate motion requests
- [ ] Jog keepalive + guaranteed jog stop on release/unload
- [ ] Structured logs include correlation IDs
- [ ] Clear user messaging for validation vs connectivity vs fault errors

---

## 12.1) Frontend state machine (recommended)

Use this minimal state machine for motion actions.

### States
- `idle`: no active command.
- `pending_ack`: command published, waiting for MQTT ACK.
- `indeterminate`: ACK error/timeout where completion is unknown (especially `504`).
- `moving`: command accepted and status indicates motion in progress.
- `completed`: target reached and motion stopped.
- `failed`: command failed definitively.

### Transitions for move
1. `idle -> pending_ack`
   - Trigger: publish `POST /drive/move_to_position`.
   - UI: disable move buttons, show spinner.

2. `pending_ack -> moving`
   - Condition: ACK `success=true` (typically `status_code=200`).
   - Action: start polling `/drive/status` every 200-500 ms.

3. `pending_ack -> indeterminate`
   - Condition: ACK timeout or `status_code=504`.
   - Action: do **not** auto-retry immediately; start status reconciliation polling.

4. `pending_ack -> failed`
   - Condition: definitive failure (`status_code=0`, validation `4xx`, explicit command reject).
   - Action: show error and keep controls safe.

5. `moving|indeterminate -> completed`
   - Condition from `/drive/status`: `status_bits.target_reached=true` and `velocity=0`.
   - Action: stop polling, unlock controls.

6. `moving|indeterminate -> failed`
   - Condition: fault state or repeated unreachable status endpoint.
   - Action: show recovery CTA (`fault_reset`, retry, operator confirmation).

### Transitions for jog
1. On press: send `/drive/jog_start`, state `pending_ack`.
2. If ACK success: state `moving`, start `jog_update` heartbeat (100-300 ms cadence for `ttl_ms=1000`).
3. On release/blur/unmount: always send `/drive/jog_stop` and return to `idle`.
4. On any jog heartbeat error: switch to `failed`, stop heartbeat, send best-effort `/drive/jog_stop`.

### Transitions for stop
- On `POST /drive/stop` success: switch to `indeterminate` briefly and confirm by polling `/drive/status` until velocity stabilizes at `0`.
- If stop returns error: keep controls locked for safety until status reconciliation completes.

### Pseudocode

```ts
if (state === 'idle' && action === 'move') {
  state = 'pending_ack'
  ack = await sendMove()

  if (ack.timeout || ack.status_code === 504) {
    state = 'indeterminate'
  } else if (ack.success) {
    state = 'moving'
  } else {
    state = 'failed'
  }

  while (state === 'moving' || state === 'indeterminate') {
    s = await getStatus()
    if (s.fault.active) { state = 'failed'; break }
    if (s.status_bits.target_reached && s.velocity === 0) { state = 'completed'; break }
  }
}
```

---

## 13) Legacy migration quick map

Do not use legacy endpoints in new frontend code.

Mapping reference:
- `/status` -> `/drive/status`
- `/move` -> `/drive/move_to_position`
- `/reference` -> `/drive/reference`
- `/fault_reset` -> `/drive/fault_reset`

---

## 14) Quick test scenarios (for frontend QA)

1. **Status read**: command `/drive/status` returns 200 and parsed state fields.
2. **Move success**: send valid move, receive ACK, then confirm completion via `/drive/status` (`target_reached=true`, `velocity=0`).
3. **Move with 504**: if request returns `504`, treat as indeterminate and verify real drive state by polling `/drive/status`.
4. **Validation fail**: send invalid payload (e.g., missing required field), verify clear UI error.
5. **Transport fail**: disconnect broker/network, verify timeout + reconnect handling.
6. **Jog hold/release**: hold sends start/update loop; release sends stop; no motion after release.
7. **Fault flow**: induce/observe fault state, run `/drive/fault_reset`, verify state recovery.

---

## 15) Minimal payload reference table

| Action | Method | Path | Required body fields |
|---|---|---|---|
| Read status | GET | `/drive/status` | none |
| Move position | POST | `/drive/move_to_position` | `target_position`, `relative`, `profile` |
| Jog start | POST | `/drive/jog_start` | `direction`, `speed`, `ttl_ms` |
| Jog update | POST | `/drive/jog_update` | `direction`, `speed`, `ttl_ms` |
| Jog stop | POST | `/drive/jog_stop` | none |
| Stop | POST | `/drive/stop` | `mode` |
| Reference | POST | `/drive/reference` | none |
| Fault reset | POST | `/drive/fault_reset` | none |

This table is intentionally concise. Use examples above as canonical payloads.
