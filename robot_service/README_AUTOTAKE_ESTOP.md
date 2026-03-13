# Autotake & E-Stop Guide (HTTP + MQTT)

Audience: frontend developers integrating autotake (product pick) and emergency stop.

---

## 1) Architecture

```text
Frontend UI
  ├─ MQTT cmd/robot     → mqtt_command_service → robot_service HTTP (port 8110)
  ├─ MQTT commands/estop → mqtt_command_service → robot_service HTTP /tasks/estop
  └─ HTTP direct        → API Gateway (port 8201) → robot_service

robot_service
  ├─ POST /move/autotake     — launch 3-stage autotake
  ├─ POST /tasks/estop       — emergency stop (bridge only)
  ├─ POST /safety/recover    — recovery after E-Stop
  ├─ GET  /tasks/current     — current task status
  └─ GET  /tasks/status/{id} — task status by task_id
```

---

## 2) Autotake — overview

Autotake performs automatic product pick-up in 3 stages:

1. **Stage 1 (far)**: distance > 250 mm — Z-axis only movement, camera keeps target in sight.
2. **Stage 2 (mid)**: 250..20 mm — re-measure depth (glare protection), apply X/Y offsets from trajectory config.
3. **Stage 3 (close)**: last 20 mm — half speed, blind approach, vacuum verification.

Prerequisite: trajectory config must be saved before calling autotake (see section 4).

---

## 3) E-Stop — overview

E-Stop immediately halts all robot subsystems. Two mechanisms:

- **MQTT `estop` command** — via `mqtt_command_service` bridge; calls `POST /tasks/estop` on robot_service.
- **Hardware E-Stop** — physical button; detected by safety state tracker (nav2adapter).

### Safety state monitoring

Subscribe to retained topic:
```
aroc/robot/{robot_id}/status/safety
```

Example payload:
```json
{
  "safety_lockout": true,
  "reason": "estop",
  "recovery_available": false,
  "timestamp": "2026-03-10T10:00:00Z"
}
```

Possible `reason` values: `estop`, `relay_open`, `sfuse_blown`, `null`.

---

## 4) Trajectory config (autotake prerequisite)

Trajectory configuration must be saved before calling autotake.

### 4.1 Read current configuration

**HTTP**
```http
GET /autotake_config/trajectory
```

**MQTT**
```json
{
  "request_id": "traj-read-001",
  "method": "GET",
  "path": "/autotake_config/trajectory",
  "timeout": 10
}
```
Topic: `aroc/robot/{robot_id}/cmd/robot`

### 4.2 Save configuration

**HTTP**
```http
POST /autotake_config/trajectory
Content-Type: application/json
```

```json
{
  "baseMove": {
    "active": true,
    "posX": 5.0,
    "posY": -2.0,
    "posZ": 10.0
  },
  "prefix": {
    "active": false,
    "posX": 0,
    "posY": 0,
    "posZ": 0
  },
  "graspAnalysis": {
    "enabled": false
  }
}
```

**MQTT**
```json
{
  "request_id": "traj-save-001",
  "method": "POST",
  "path": "/autotake_config/trajectory",
  "body": {
    "baseMove": {
      "active": true,
      "posX": 5.0,
      "posY": -2.0,
      "posZ": 10.0
    },
    "prefix": {
      "active": false,
      "posX": 0,
      "posY": 0,
      "posZ": 0
    }
  },
  "timeout": 10
}
```
Topic: `aroc/robot/{robot_id}/cmd/robot`

---

## 5) Autotake — invocation

### 5.1 HTTP

```http
POST /move/autotake
Content-Type: application/json
```

```json
{
  "velocity_percent": 20
}
```

- `velocity_percent` — speed 1..100%.
- Endpoint returns `task_id` (task runs asynchronously).

Base URLs:
- Direct: `http://<host>:8110/move/autotake`
- Via API Gateway: `http://<gateway>:8201/api/v1/robot/move/autotake`

### 5.2 MQTT (via HTTP proxy bridge)

Topic: `aroc/robot/{robot_id}/cmd/robot`

```json
{
  "request_id": "autotake-001",
  "method": "POST",
  "path": "/move/autotake",
  "body": {
    "velocity_percent": 20
  },
  "timeout": 120
}
```

> **Important**: autotake is a long-running operation (up to 60+ seconds). Use `timeout: 120`.

### 5.3 Response (ACK)

```json
{
  "type": "ack",
  "request_id": "autotake-001",
  "service": "robot",
  "success": true,
  "status_code": 200,
  "body": {
    "task_id": "abc123",
    "status": "running",
    "result": null
  },
  "error": null,
  "timestamp": "2026-03-10T10:00:05Z"
}
```

ACK means the task was **accepted**. To track completion, poll the task status (section 5.4).

### 5.4 Task status tracking

**HTTP**
```http
GET /tasks/current
```

or by specific task_id:
```http
GET /tasks/status/{task_id}
```

**MQTT**
```json
{
  "request_id": "task-poll-001",
  "method": "GET",
  "path": "/tasks/current",
  "timeout": 10
}
```
Topic: `aroc/robot/{robot_id}/cmd/robot`

Example response (task completed):
```json
{
  "task_id": "abc123",
  "status": "finished",
  "result": {
    "success": true
  }
}
```

Possible statuses: `running`, `finished`, `failed`, `cancelled`, `not_found`.

---

## 6) E-Stop — invocation

### 6.1 MQTT — dedicated command topic (recommended)

Topic: `aroc/robot/{robot_id}/commands/estop`

```json
{
  "command_id": "estop-001",
  "timestamp": "2026-03-10T10:05:00Z",
  "reason": "Emergency stop button pressed"
}
```

Fields:
- `command_id` (required) — unique ID.
- `reason` (optional) — stop reason.
- `timestamp` (optional) — command timestamp.

Response arrives on: `aroc/robot/{robot_id}/status/navigation`

### 6.2 MQTT — via HTTP proxy bridge

Topic: `aroc/robot/{robot_id}/cmd/robot`

```json
{
  "request_id": "estop-http-001",
  "method": "POST",
  "path": "/tasks/estop",
  "body": {
    "reason": "Operator emergency stop"
  },
  "timeout": 10
}
```

Response arrives on: `aroc/robot/{robot_id}/resp/robot`

### 6.3 HTTP direct

```http
POST /tasks/estop
Content-Type: application/json
```

```json
{
  "reason": "Operator emergency stop"
}
```

Base URLs:
- Direct: `http://<host>:8110/tasks/estop`
- Via API Gateway: `http://<gateway>:8201/api/v1/robot/tasks/estop`

---

## 7) Recovery after E-Stop

After releasing the hardware E-Stop button or after a software estop, sequential recovery is required.

### 7.1 HTTP

```http
POST /safety/recover
```

Base URLs:
- Direct: `http://<host>:8110/safety/recover`
- Via API Gateway: `http://<gateway>:8201/api/v1/robot/safety/recover`

### 7.2 MQTT

Topic: `aroc/robot/{robot_id}/cmd/robot`

```json
{
  "request_id": "recover-001",
  "method": "POST",
  "path": "/safety/recover",
  "body": {},
  "timeout": 30
}
```

### 7.3 Recovery response

```json
{
  "success": true,
  "steps": [
    {"name": "check_safety_relay", "status": "ok"},
    {"name": "igus_fault_reset", "status": "ok"},
    {"name": "xarm_recover", "status": "ok"},
    {"name": "xarm_enable_motion", "status": "ok"},
    {"name": "drive_mode_enable", "status": "ok"}
  ],
  "message": "Recovery completed"
}
```

Recovery steps:
1. Check safety relay (must be closed — E-Stop released).
2. IGUS fault_reset.
3. xArm recover + enable_motion.
4. Drive mode enable (Symovo).

If the relay is still open, recovery returns:
```json
{
  "success": false,
  "steps": [
    {"name": "check_safety_relay", "status": "failed", "error": "safety lockout active"}
  ],
  "message": "Safety relay still open — release E-Stop first"
}
```

---

## 8) CLI examples (mosquitto)

Connection details:
- MQTT broker: `82.165.177.194:8883` (TLS)
- Username: `bridge_user`
- Password: `G456AH37gbc`
- Robot ID: `fahrdummy-01`

### A) Subscribe to responses

```bash
# HTTP proxy responses (robot service)
mosquitto_sub -h 82.165.177.194 -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'G456AH37gbc' \
  -t "aroc/robot/fahrdummy-01/resp/robot" -v

# Safety state (retained)
mosquitto_sub -h 82.165.177.194 -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'G456AH37gbc' \
  -t "aroc/robot/fahrdummy-01/status/safety" -v

# Navigation status (estop results)
mosquitto_sub -h 82.165.177.194 -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'G456AH37gbc' \
  -t "aroc/robot/fahrdummy-01/status/navigation" -v
```

### B) Autotake

```bash
mosquitto_pub -h 82.165.177.194 -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'G456AH37gbc' \
  -t "aroc/robot/fahrdummy-01/cmd/robot" \
  -m '{
    "request_id": "autotake-001",
    "method": "POST",
    "path": "/move/autotake",
    "body": {
      "velocity_percent": 20
    },
    "timeout": 120
  }'
```

### C) E-Stop (dedicated command)

```bash
mosquitto_pub -h 82.165.177.194 -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'G456AH37gbc' \
  -t "aroc/robot/fahrdummy-01/commands/estop" \
  -m '{
    "command_id": "estop-001",
    "reason": "Emergency stop"
  }'
```

### D) Check current task status

```bash
mosquitto_pub -h 82.165.177.194 -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'G456AH37gbc' \
  -t "aroc/robot/fahrdummy-01/cmd/robot" \
  -m '{
    "request_id": "task-check-001",
    "method": "GET",
    "path": "/tasks/current",
    "timeout": 10
  }'
```

### E) Safety recover

```bash
mosquitto_pub -h 82.165.177.194 -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'G456AH37gbc' \
  -t "aroc/robot/fahrdummy-01/cmd/robot" \
  -m '{
    "request_id": "recover-001",
    "method": "POST",
    "path": "/safety/recover",
    "body": {},
    "timeout": 30
  }'
```

### F) Read safety state

```bash
mosquitto_pub -h 82.165.177.194 -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'G456AH37gbc' \
  -t "aroc/robot/fahrdummy-01/cmd/robot" \
  -m '{
    "request_id": "safety-state-001",
    "method": "GET",
    "path": "/safety/state",
    "timeout": 10
  }'
```

> **Note**: `/safety/state` is a nav2adapter endpoint (port 7905). Via MQTT proxy, send to `cmd/symovo`, not `cmd/robot`:

```bash
mosquitto_pub -h 82.165.177.194 -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'G456AH37gbc' \
  -t "aroc/robot/fahrdummy-01/cmd/symovo" \
  -m '{
    "request_id": "safety-state-001",
    "method": "GET",
    "path": "/safety/state",
    "timeout": 10
  }'
```

---

## 9) Frontend JavaScript

```js
import mqtt from 'mqtt';

const robotId = 'fahrdummy-01';
const cmdRobotTopic = `aroc/robot/${robotId}/cmd/robot`;
const cmdEstopTopic = `aroc/robot/${robotId}/commands/estop`;
const respRobotTopic = `aroc/robot/${robotId}/resp/robot`;
const safetyTopic = `aroc/robot/${robotId}/status/safety`;
const navStatusTopic = `aroc/robot/${robotId}/status/navigation`;

const client = mqtt.connect('wss://mqtt.example.com:8083', {
  username: 'bridge_user',
  password: 'G456AH37gbc',
  reconnectPeriod: 2000,
});

const pending = new Map();

client.on('connect', () => {
  client.subscribe([respRobotTopic, safetyTopic, navStatusTopic]);
});

client.on('message', (topic, payload) => {
  let msg;
  try { msg = JSON.parse(payload.toString('utf-8')); } catch { return; }

  // Safety state update
  if (topic === safetyTopic) {
    handleSafetyState(msg);
    return;
  }

  // Navigation status (estop command results)
  if (topic === navStatusTopic) {
    handleNavStatus(msg);
    return;
  }

  // HTTP proxy responses
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
    reject(new Error(msg.error?.message || msg.body?.detail || `HTTP ${msg.status_code}`));
    return;
  }
  resolve(msg.body);
});

function sendRobotCommand({ method, path, body, timeout = 15 }) {
  const requestId = crypto.randomUUID();
  const envelope = {
    request_id: requestId,
    method,
    path,
    timeout,
    ...(body !== undefined ? { body } : {}),
  };
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pending.delete(requestId);
      reject(new Error('Client timeout'));
    }, (timeout + 5) * 1000);
    pending.set(requestId, { resolve, reject, timer });
    client.publish(cmdRobotTopic, JSON.stringify(envelope), { qos: 1 });
  });
}

// ── Autotake ─────────────────────────────────────────────────
async function startAutotake(velocityPercent = 20) {
  return sendRobotCommand({
    method: 'POST',
    path: '/move/autotake',
    body: { velocity_percent: velocityPercent },
    timeout: 120,
  });
}

async function pollTaskUntilDone(intervalMs = 1000, maxAttempts = 120) {
  for (let i = 0; i < maxAttempts; i++) {
    const result = await sendRobotCommand({
      method: 'GET',
      path: '/tasks/current',
      timeout: 10,
    });
    if (result.status === 'finished') return result;
    if (result.status === 'failed') throw new Error('Task failed');
    if (result.status === 'cancelled') throw new Error('Task cancelled');
    await new Promise(r => setTimeout(r, intervalMs));
  }
  throw new Error('Task polling timeout');
}

// ── E-Stop ───────────────────────────────────────────────────
function sendEstop(reason = 'UI emergency stop') {
  const envelope = {
    command_id: crypto.randomUUID(),
    reason,
  };
  client.publish(cmdEstopTopic, JSON.stringify(envelope), { qos: 1 });
}

// ── Recovery ─────────────────────────────────────────────────
async function recoverFromEstop() {
  return sendRobotCommand({
    method: 'POST',
    path: '/safety/recover',
    body: {},
    timeout: 30,
  });
}

// ── Safety state handler ─────────────────────────────────────
function handleSafetyState(msg) {
  if (msg.safety_lockout) {
    // Lock all motion controls
    // Display reason: msg.reason
    disableMotionControls(msg.reason);
  } else {
    enableMotionControls();
  }
}

function handleNavStatus(msg) {
  // Handle navigation/estop status updates
  console.log('Navigation status:', msg);
}
```

### Full autotake flow example:

```js
try {
  disableMotionControls('autotake running');
  await startAutotake(20);
  const result = await pollTaskUntilDone();
  console.log('Autotake completed:', result);
} catch (err) {
  console.error('Autotake failed:', err.message);
} finally {
  enableMotionControls();
}
```

---

## 10) Frontend state machine

### States

- `idle` — no active operation.
- `autotake_pending` — autotake started, waiting for ACK.
- `autotake_running` — ACK received, task in progress.
- `estop_active` — E-Stop active (safety lockout).
- `recovering` — recovery in progress.
- `completed` — operation completed successfully.
- `failed` — operation failed.

### Transitions (autotake)

```
idle → autotake_pending          [POST /move/autotake]
autotake_pending → autotake_running  [ACK success=true]
autotake_pending → failed            [ACK error / timeout]
autotake_running → completed         [poll: status=finished]
autotake_running → failed            [poll: status=failed]
autotake_running → estop_active      [safety_lockout=true]
```

### Transitions (E-Stop)

```
any_state → estop_active          [safety_lockout=true OR estop command sent]
estop_active → recovering         [POST /safety/recover]
recovering → idle                 [recovery success=true]
recovering → estop_active         [recovery failed — relay still open]
```

### UI rules

1. When `estop_active` — **all** motion buttons are locked. Show "Release E-Stop and recover".
2. When `autotake_running` — lock move/autotake buttons. E-Stop button always available.
3. E-Stop button is **never locked** — always active.

---

## 11) Error handling

### Transport (MQTT/bridge)
- `status_code: 0` or client timeout.
- Action: reconnect + retry with backoff.

### HTTP errors
- `4xx` — invalid request (check payload).
- `503` — service unavailable.
- `504` — gateway timeout; for autotake this may mean the task is still running. Check via `/tasks/current`.

### Autotake domain errors
- Depth camera unavailable — autotake aborted.
- Distance out of range (30..1000 mm) — autotake aborted.
- Vacuum not confirmed — product not gripped.

### Safety lockout
- On receiving `safety_lockout: true` — immediately lock all motion controls.
- Wait for physical E-Stop release, then call `/safety/recover`.

---

## 12) Real-world scenarios — step by step

### 12.1 Scenario: Full autotake cycle (MQTT)

Complete flow from trajectory setup to product pick confirmation.

Use two terminals:
1. Terminal A: subscribe to responses.
2. Terminal B: publish commands.

#### Step 1 — Subscribe to robot responses

```bash
mosquitto_sub -h 82.165.177.194 -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'G456AH37gbc' \
  -t "aroc/robot/fahrdummy-01/resp/robot" -v
```

#### Step 2 — Read current trajectory config

```bash
mosquitto_pub -h 82.165.177.194 -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'G456AH37gbc' \
  -t "aroc/robot/fahrdummy-01/cmd/robot" \
  -m '{
    "request_id": "traj-read-001",
    "method": "GET",
    "path": "/autotake_config/trajectory",
    "timeout": 10
  }'
```

Expected response on `resp/robot`:
```json
{
  "type": "ack",
  "request_id": "traj-read-001",
  "service": "robot",
  "success": true,
  "status_code": 200,
  "body": {
    "prefix": {"active": false, "posX": 0, "posY": 0, "posZ": 0, "speed": 100},
    "postfix": {"active": false, "posX": 0, "posY": 0, "posZ": 0, "speed": 100},
    "baseMove": {"active": true, "posX": -30, "posY": 52, "posZ": -10, "speed": 100},
    "gripper": {"active": true},
    "gripperVerify": {"samples": 3, "required": 2, "intervalMs": 120, "maxReadErrors": 3},
    "liftTest": {"enabled": true, "liftMm": 8.0, "holdMs": 220, "samples": 2, "required": 2},
    "graspAnalysis": {"enabled": false},
    "return": {"active": true}
  },
  "error": null,
  "timestamp": "2026-03-10T10:00:01.230Z"
}
```

#### Step 3 — Save updated trajectory config (if needed)

```bash
mosquitto_pub -h 82.165.177.194 -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'G456AH37gbc' \
  -t "aroc/robot/fahrdummy-01/cmd/robot" \
  -m '{
    "request_id": "traj-save-001",
    "method": "POST",
    "path": "/autotake_config/trajectory",
    "body": {
      "prefix": {"active": false, "posX": 0, "posY": 0, "posZ": 0, "speed": 100},
      "baseMove": {"active": true, "posX": -30, "posY": 52, "posZ": -10, "speed": 100},
      "gripper": {"active": true},
      "gripperVerify": {"samples": 3, "required": 2, "intervalMs": 120, "maxReadErrors": 3},
      "liftTest": {"enabled": true, "liftMm": 8.0, "holdMs": 220, "samples": 2, "required": 2},
      "graspAnalysis": {"enabled": false},
      "return": {"active": true}
    },
    "timeout": 10
  }'
```

Expected response:
```json
{
  "type": "ack",
  "request_id": "traj-save-001",
  "service": "robot",
  "success": true,
  "status_code": 201,
  "body": {"status": "ok", "message": "Trajectory configuration saved."},
  "error": null
}
```

#### Step 4 — Start autotake

```bash
mosquitto_pub -h 82.165.177.194 -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'G456AH37gbc' \
  -t "aroc/robot/fahrdummy-01/cmd/robot" \
  -m '{
    "request_id": "autotake-001",
    "method": "POST",
    "path": "/move/autotake",
    "body": {"velocity_percent": 20},
    "timeout": 120
  }'
```

Expected ACK (task accepted):
```json
{
  "type": "ack",
  "request_id": "autotake-001",
  "service": "robot",
  "success": true,
  "status_code": 200,
  "body": {
    "success": true,
    "task_id": "a1b2c3d4e5f6",
    "result": null,
    "detail": "working"
  },
  "error": null,
  "timestamp": "2026-03-10T10:01:00.112Z"
}
```

#### Step 5 — Poll task status until completion

```bash
# Repeat every 1-2 seconds
mosquitto_pub -h 82.165.177.194 -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'G456AH37gbc' \
  -t "aroc/robot/fahrdummy-01/cmd/robot" \
  -m '{
    "request_id": "task-poll-001",
    "method": "GET",
    "path": "/tasks/status/a1b2c3d4e5f6",
    "timeout": 10
  }'
```

Response while running:
```json
{
  "type": "ack",
  "request_id": "task-poll-001",
  "service": "robot",
  "success": true,
  "status_code": 200,
  "body": {"status": "working", "result": null},
  "error": null
}
```

Response when finished:
```json
{
  "type": "ack",
  "request_id": "task-poll-002",
  "service": "robot",
  "success": true,
  "status_code": 200,
  "body": {"status": "finished", "result": {"result": true}},
  "error": null
}
```

Response on failure:
```json
{
  "type": "ack",
  "request_id": "task-poll-003",
  "service": "robot",
  "success": true,
  "status_code": 200,
  "body": {"status": "error", "result": {"error": "Autotake operation failed: Depth camera unavailable"}},
  "error": null
}
```

---

### 12.2 Scenario: E-Stop during autotake (MQTT)

#### Step 1 — Subscribe to safety + navigation + responses

```bash
mosquitto_sub -h 82.165.177.194 -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'G456AH37gbc' \
  -t "aroc/robot/fahrdummy-01/resp/robot" \
  -t "aroc/robot/fahrdummy-01/status/safety" \
  -t "aroc/robot/fahrdummy-01/status/navigation" -v
```

#### Step 2 — Start autotake (same as 12.1 Step 4)

```bash
mosquitto_pub -h 82.165.177.194 -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'G456AH37gbc' \
  -t "aroc/robot/fahrdummy-01/cmd/robot" \
  -m '{
    "request_id": "autotake-002",
    "method": "POST",
    "path": "/move/autotake",
    "body": {"velocity_percent": 20},
    "timeout": 120
  }'
```

#### Step 3 — Send E-Stop while autotake is running

```bash
mosquitto_pub -h 82.165.177.194 -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'G456AH37gbc' \
  -t "aroc/robot/fahrdummy-01/commands/estop" \
  -m '{
    "command_id": "estop-002",
    "reason": "Operator pressed emergency stop during autotake"
  }'
```

Expected on `status/navigation`:
```json
{
  "state": "acknowledged",
  "command_name": "estop",
  "command_id": "estop-002",
  "success": true,
  "timestamp": "2026-03-10T10:01:15.340Z"
}
```

Expected on `status/safety` (retained):
```json
{
  "safety_lockout": true,
  "reason": "estop",
  "recovery_available": false,
  "timestamp": "2026-03-10T10:01:15.350Z"
}
```

#### Step 4 — Verify autotake task is aborted

```bash
mosquitto_pub -h 82.165.177.194 -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'G456AH37gbc' \
  -t "aroc/robot/fahrdummy-01/cmd/robot" \
  -m '{
    "request_id": "task-check-002",
    "method": "GET",
    "path": "/tasks/current",
    "timeout": 10
  }'
```

Expected: task is `error` or `not_found` (already aborted):
```json
{
  "type": "ack",
  "request_id": "task-check-002",
  "service": "robot",
  "success": true,
  "status_code": 200,
  "body": {"task_id": null, "status": "not_found", "result": null},
  "error": null
}
```

---

### 12.3 Scenario: Recovery after E-Stop (MQTT)

Continues from 12.2 — E-Stop was triggered, now recover.

#### Step 1 — Check safety state before recovery

```bash
mosquitto_pub -h 82.165.177.194 -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'G456AH37gbc' \
  -t "aroc/robot/fahrdummy-01/cmd/symovo" \
  -m '{
    "request_id": "safety-check-001",
    "method": "GET",
    "path": "/safety/state",
    "timeout": 10
  }'
```

Response on `resp/symovo` — relay still open (E-Stop still held):
```json
{
  "type": "ack",
  "request_id": "safety-check-001",
  "service": "symovo",
  "success": true,
  "status_code": 200,
  "body": {
    "status": "locked",
    "safety_lockout": true,
    "reason": "estop",
    "recovery_available": false,
    "state_flags": {
      "emergency_stop": true,
      "safety_relais_closed_state": false,
      "sfuse_blown": false
    }
  },
  "error": null
}
```

Response after E-Stop button released — relay closed, recovery available:
```json
{
  "type": "ack",
  "request_id": "safety-check-002",
  "service": "symovo",
  "success": true,
  "status_code": 200,
  "body": {
    "status": "ok",
    "safety_lockout": false,
    "reason": null,
    "recovery_available": true,
    "state_flags": {
      "emergency_stop": false,
      "safety_relais_closed_state": true,
      "sfuse_blown": false
    }
  },
  "error": null
}
```

#### Step 2 — Run full recovery

```bash
mosquitto_pub -h 82.165.177.194 -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'G456AH37gbc' \
  -t "aroc/robot/fahrdummy-01/cmd/robot" \
  -m '{
    "request_id": "recover-001",
    "method": "POST",
    "path": "/safety/recover",
    "body": {},
    "timeout": 30
  }'
```

Success response on `resp/robot`:
```json
{
  "type": "ack",
  "request_id": "recover-001",
  "service": "robot",
  "success": true,
  "status_code": 200,
  "body": {
    "success": true,
    "steps": [
      {"name": "check_safety_relay", "status": "ok"},
      {"name": "igus_fault_reset", "status": "ok"},
      {"name": "xarm_recover", "status": "ok"},
      {"name": "xarm_enable_motion", "status": "ok"},
      {"name": "drive_mode_enable", "status": "ok"}
    ],
    "message": "Recovery completed"
  },
  "error": null
}
```

Failure response (E-Stop still held):
```json
{
  "type": "ack",
  "request_id": "recover-002",
  "service": "robot",
  "success": true,
  "status_code": 200,
  "body": {
    "success": false,
    "steps": [
      {"name": "check_safety_relay", "status": "failed", "error": "safety lockout active"}
    ],
    "message": "Safety relay still open — release E-Stop first"
  },
  "error": null
}
```

#### Step 3 — Confirm robot is operational again

```bash
mosquitto_pub -h 82.165.177.194 -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'G456AH37gbc' \
  -t "aroc/robot/fahrdummy-01/cmd/robot" \
  -m '{
    "request_id": "status-after-recover-001",
    "method": "GET",
    "path": "/tasks/current",
    "timeout": 10
  }'
```

Expected — idle (no running task):
```json
{
  "type": "ack",
  "request_id": "status-after-recover-001",
  "service": "robot",
  "success": true,
  "status_code": 200,
  "body": {"task_id": null, "status": "not_found", "result": null},
  "error": null
}
```

Robot is now ready for new autotake or other operations.

---

### 12.4 Scenario: Autotake rejected — device busy (MQTT)

When a previous task is still running:

```bash
mosquitto_pub -h 82.165.177.194 -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'G456AH37gbc' \
  -t "aroc/robot/fahrdummy-01/cmd/robot" \
  -m '{
    "request_id": "autotake-busy-001",
    "method": "POST",
    "path": "/move/autotake",
    "body": {"velocity_percent": 20},
    "timeout": 120
  }'
```

Expected 202 response:
```json
{
  "type": "ack",
  "request_id": "autotake-busy-001",
  "service": "robot",
  "success": false,
  "status_code": 202,
  "body": {"error": "Device is busy", "task_id": "a1b2c3d4e5f6"},
  "error": {"type": "http_error", "message": "HTTP 202"},
  "timestamp": "2026-03-10T10:02:00.445Z"
}
```

Frontend: show "Operation already in progress", do not retry, wait for current task to finish.

---

### 12.5 Scenario: Autotake rejected — safety lockout (MQTT)

When robot is in safety lockout (E-Stop active):

```bash
mosquitto_pub -h 82.165.177.194 -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'G456AH37gbc' \
  -t "aroc/robot/fahrdummy-01/cmd/robot" \
  -m '{
    "request_id": "autotake-locked-001",
    "method": "POST",
    "path": "/move/autotake",
    "body": {"velocity_percent": 20},
    "timeout": 120
  }'
```

Expected 423 response:
```json
{
  "type": "ack",
  "request_id": "autotake-locked-001",
  "service": "robot",
  "success": false,
  "status_code": 423,
  "body": {"error": "Safety lockout: estop", "safety_lockout": true},
  "error": {"type": "http_error", "message": "HTTP 423"},
  "timestamp": "2026-03-10T10:03:00.120Z"
}
```

Frontend: show "Safety lockout active — release E-Stop and recover", disable all motion buttons.

---

### 12.6 Scenario: Full autotake cycle (HTTP / curl)

For direct HTTP testing without MQTT.

#### Read trajectory config

```bash
curl -s http://192.168.1.10:8110/autotake_config/trajectory | python3 -m json.tool
```

#### Save trajectory config

```bash
curl -s -X POST http://192.168.1.10:8110/autotake_config/trajectory \
  -H "Content-Type: application/json" \
  -d '{
    "baseMove": {"active": true, "posX": -30, "posY": 52, "posZ": -10, "speed": 100},
    "gripper": {"active": true},
    "gripperVerify": {"samples": 3, "required": 2, "intervalMs": 120, "maxReadErrors": 3},
    "liftTest": {"enabled": true, "liftMm": 8.0, "holdMs": 220},
    "graspAnalysis": {"enabled": false},
    "return": {"active": true}
  }' | python3 -m json.tool
```

Expected:
```json
{
  "status": "ok",
  "message": "Trajectory configuration saved."
}
```

#### Start autotake

```bash
curl -s -X POST http://192.168.1.10:8110/move/autotake \
  -H "Content-Type: application/json" \
  -d '{"velocity_percent": 20}' | python3 -m json.tool
```

Expected:
```json
{
  "success": true,
  "task_id": "a1b2c3d4e5f6",
  "result": null,
  "detail": "working",
  "error": null
}
```

#### Poll task status

```bash
# By task_id
curl -s http://192.168.1.10:8110/tasks/status/a1b2c3d4e5f6 | python3 -m json.tool

# Or current task
curl -s http://192.168.1.10:8110/tasks/current | python3 -m json.tool
```

Working:
```json
{"status": "working", "result": null}
```

Finished:
```json
{"status": "finished", "result": {"result": true}}
```

Error:
```json
{"status": "error", "result": {"error": "Autotake operation failed: Distance 1200.0 mm exceeds 1000 mm limit"}}
```

#### Cancel current task

```bash
curl -s -X POST http://192.168.1.10:8110/tasks/cancel_current | python3 -m json.tool
```

---

### 12.7 Scenario: E-Stop + recovery (HTTP / curl)

#### Send E-Stop

```bash
curl -s -X POST http://192.168.1.10:8110/tasks/estop \
  -H "Content-Type: application/json" \
  -d '{"reason": "Operator emergency"}' | python3 -m json.tool
```

#### Check safety state (via nav2adapter)

```bash
curl -s http://192.168.1.10:7905/safety/state | python3 -m json.tool
```

Expected while E-Stop active:
```json
{
  "status": "locked",
  "safety_lockout": true,
  "reason": "estop",
  "recovery_available": false,
  "state_flags": {
    "emergency_stop": true,
    "safety_relais_closed_state": false
  }
}
```

#### Run recovery (after releasing E-Stop)

```bash
curl -s -X POST http://192.168.1.10:8110/safety/recover | python3 -m json.tool
```

Expected:
```json
{
  "success": true,
  "steps": [
    {"name": "check_safety_relay", "status": "ok"},
    {"name": "igus_fault_reset", "status": "ok"},
    {"name": "xarm_recover", "status": "ok"},
    {"name": "xarm_enable_motion", "status": "ok"},
    {"name": "drive_mode_enable", "status": "ok"}
  ],
  "message": "Recovery completed"
}
```

#### Via API Gateway

Same commands but through API Gateway:
```bash
curl -s -X POST http://192.168.1.10:8201/api/v1/robot/move/autotake \
  -H "Content-Type: application/json" \
  -d '{"velocity_percent": 20}' | python3 -m json.tool

curl -s -X POST http://192.168.1.10:8201/api/v1/robot/tasks/estop \
  -H "Content-Type: application/json" \
  -d '{"reason": "Operator emergency"}' | python3 -m json.tool

curl -s -X POST http://192.168.1.10:8201/api/v1/robot/safety/recover | python3 -m json.tool
```

---

## 13) Correlation and tracing

Use these fields consistently for debugging:
- **`request_id`**: primary MQTT request/response correlation (frontend-generated).
- **`task_id`**: returned by autotake and other async operations — used to poll task completion.
- **`command_id`**: used in dedicated command topics (`commands/estop`).

Recommended log pattern:
```
{ request_id, task_id, command_id, path, status_code, timestamp }
```

For each operation, log at minimum:
1. Command sent: `{ request_id, method, path, timestamp }`
2. ACK received: `{ request_id, status_code, task_id, success }`
3. Task completion: `{ task_id, status, result }`

---

## 14) Timeouts, retries, and idempotency

### Timeout layers
- **Frontend promise timeout**: must be slightly larger than envelope `timeout` (e.g. `timeout + 5` seconds).
- **Bridge HTTP timeout**: `timeout` field in MQTT envelope (seconds).
- **Autotake internal timeouts**: depth camera measurement, vacuum verification, xArm motion — all handled internally.

### Retry policy
- Safe to retry: `GET /tasks/current`, `GET /tasks/status/{id}`, `GET /autotake_config/trajectory`.
- **Do NOT retry** `POST /move/autotake` without confirming previous task is complete. Always check `/tasks/current` first.
- E-Stop (`commands/estop`): safe to send multiple times — bridge dedup prevents duplicate processing.
- Recovery (`POST /safety/recover`): safe to retry — idempotent when already recovered.

### Idempotency notes
- Each `POST /move/autotake` starts a **new** task. The `tasked_getter` decorator rejects with 202 if a task is already running.
- On bridge side, `request_id` dedup prevents re-processing the same MQTT message.
- Frontend should: disable autotake button while task is active, re-enable only on `finished`/`error`/`cancelled`.

---

## 15) Autotake UX checklist

When implementing autotake button in UI:

- [ ] Trajectory config is saved before first autotake attempt
- [ ] Autotake button is disabled while task is `working`/`pending`
- [ ] E-Stop button is **always** enabled regardless of autotake state
- [ ] On ACK received: store `task_id`, start polling `/tasks/status/{task_id}` every 1-2 seconds
- [ ] On task `finished`: show success, re-enable controls
- [ ] On task `error`: show error message from `result.error`, re-enable controls
- [ ] On 202 (busy): show "Operation in progress", do not retry
- [ ] On 423 (safety lockout): show "E-Stop active", guide user to release + recover
- [ ] On 504 (gateway timeout): do **not** auto-retry — check `/tasks/current` to see if task is still running
- [ ] On safety_lockout event: immediately disable all motion controls

---

## 16) Production readiness checklist

- [ ] All autotake calls use `timeout: 120` (long-running operation)
- [ ] Unique `request_id` for every MQTT publish
- [ ] Frontend timeout > bridge timeout
- [ ] Proper parsing for `status_code: 0` transport errors
- [ ] Button lock/debounce to prevent duplicate autotake requests
- [ ] E-Stop button never disabled — always accessible
- [ ] Task polling stops on terminal states (`finished`, `error`, `cancelled`)
- [ ] Safety state subscription active on connect (`status/safety` retained topic)
- [ ] Clear user messaging: distinguish "busy" vs "safety lockout" vs "camera unavailable" vs "transport error"
- [ ] Structured logs include `request_id`, `task_id`, `command_id`

---

## 17) Endpoint reference table

| Action | Protocol | Method | Path / Topic | Required fields |
|---|---|---|---|---|
| Autotake | HTTP | POST | `/move/autotake` | `velocity_percent` |
| Autotake | MQTT proxy | POST | `cmd/robot` → path `/move/autotake` | `velocity_percent` |
| E-Stop | MQTT command | — | `commands/estop` | `command_id` |
| E-Stop | MQTT proxy | POST | `cmd/robot` → path `/tasks/estop` | — |
| E-Stop | HTTP | POST | `/tasks/estop` | — |
| Recovery | HTTP | POST | `/safety/recover` | — |
| Recovery | MQTT proxy | POST | `cmd/robot` → path `/safety/recover` | — |
| Task status | HTTP | GET | `/tasks/current` | — |
| Task by ID | HTTP | GET | `/tasks/status/{task_id}` | — |
| Task cancel | HTTP | POST | `/tasks/cancel_current` | — |
| Task cancel by ID | HTTP | POST | `/tasks/cancel/{task_id}` | — |
| Trajectory read | HTTP | GET | `/autotake_config/trajectory` | — |
| Trajectory save | HTTP | POST | `/autotake_config/trajectory` | config body |
| Safety state | HTTP | GET | `/safety/state` (nav2adapter :7905) | — |
| Safety state | MQTT sub | — | `status/safety` (retained) | — |
| Safety state | MQTT proxy | GET | `cmd/symovo` → path `/safety/state` | — |

---

## 18) Quick test scenarios (for frontend QA)

1. **Autotake success**: send autotake → receive ACK with `task_id` → poll `tasks/status/{id}` until `finished` → verify `result.result = true`.
2. **Autotake + E-Stop**: start autotake → send estop → verify task aborted + `safety_lockout=true` on `status/safety`.
3. **E-Stop → recovery**: send estop → release physical button → call `/safety/recover` → verify all steps `ok`.
4. **Recovery blocked**: call `/safety/recover` while E-Stop held → receive `"Safety relay still open"`.
5. **Device busy**: start autotake → immediately send second autotake → verify 202 with `"Device is busy"`.
6. **Safety lockout reject**: activate E-Stop → send autotake → verify 423 with `"Safety lockout"`.
7. **Invalid velocity**: send autotake with `velocity_percent: 0` → receive 422.
8. **Trajectory missing**: delete trajectory → call autotake → verify error.
9. **Transport fail**: disconnect broker → verify client timeout + reconnect handling.
10. **Task cancel**: start autotake → call `/tasks/cancel_current` → verify task transitions to `cancelled`.
