# Robot API — Frontend Developer Guide

Guide for frontend developers integrating with the AROC robot platform.

## Connection Details

### HTTP

| Service | Base URL |
|---------|----------|
| Robot Service | `https://api.techvisioncloud.pl/api/v1/robot` |
| Nav2adapter (Symovo) | `https://api.techvisioncloud.pl/api/v1/symovo` |

### MQTT Broker

- **Host:** `82.165.177.194`
- **Port:** `8883` (TLS)
- **Username:** `<MQTT_USER>` (from administrator)
- **Password:** `<MQTT_PASSWORD>` (from administrator)
- **Robot ID:** `fahrdummy-01`

All examples below use **full URLs** for copy-paste.

---

## 1. Waypoints (Saved Positions)

Waypoints store the full robot state: AGV pose, lift height, arm joint angles.

### List all waypoints

```bash
curl https://api.techvisioncloud.pl/api/v1/robot/waypoints/list
```

Response:
```json
[
  {
    "id": "91ec630e",
    "name": "Shelf A Row 3",
    "params": {
      "location": {"x_m": 1.73, "y_m": -0.14, "theta_deg": 0, "map_id": 0},
      "lift_position_cm": 40.0,
      "xarm_joints": {"j1": 159.68, "j2": -7.47, "j3": 0, "j4": 0, "j5": 0, "j6": 0}
    }
  },
  {
    "id": "b16f0fed",
    "name": "Drop Zone 1",
    "params": {"..."}
  }
]
```

### Record current position (saves where the robot is right now)

```bash
curl -X POST https://api.techvisioncloud.pl/api/v1/robot/waypoints/record \
  -H "Content-Type: application/json" \
  -d '{"name": "Shelf B Row 1", "velocity_percent": 25}'
```

Response:
```json
{"status": "ok", "id": "a3f2c891", "message": "Position recorded"}
```

### Delete waypoint

```bash
curl -X POST "https://api.techvisioncloud.pl/api/v1/robot/waypoints/delete?position_id=91ec630e"
```

### Update waypoint name or speed

```bash
curl -X PUT "https://api.techvisioncloud.pl/api/v1/robot/waypoints/update?position_id=91ec630e" \
  -H "Content-Type: application/json" \
  -d '{"name": "New Name", "velocity_percent": 30}'
```

---

## 2. Move to Product (Full Orchestration)

**This is the main command.** Moves the entire robot (AGV + lift + arm) to a saved waypoint.

### What the robot does automatically

When you send "move to product", the robot **prepares itself** before driving:

1. Deactivates charging stations (so the AGV won't return to dock)
2. Resets faults on arm and lift
3. Stows the arm to safe transport position
4. Lowers the lift if too high
5. Drives the AGV to the target
6. Raises the lift to target height
7. Positions the arm at target angles

**You don't need to do any of these steps manually.**

### Send robot to a saved waypoint (HTTP)

```bash
curl -X POST "https://api.techvisioncloud.pl/api/v1/robot/waypoints/run?position_id=91ec630e"
```

Response (task runs in background):
```json
{"success": true, "task_id": "t-87a3f2c1"}
```

### Send robot to a saved waypoint (MQTT)

```bash
mosquitto_pub -h 82.165.177.194 -p 8883 \
  --cafile ca.crt \
  -u <MQTT_USER> -P '<MQTT_PASSWORD>' \
  -t "aroc/robot/fahrdummy-01/commands/navigateTo" \
  -m '{
    "command_id": "nav-001",
    "target_id": "91ec630e",
    "timestamp": "2026-03-18T12:00:00Z"
  }'
```

`target_id` = waypoint `id` from `/waypoints/list`.

### Poll task status

```bash
curl https://api.techvisioncloud.pl/api/v1/robot/tasks/status/t-87a3f2c1
```

```json
{"status": "working", "result": null}
```

Poll every 1-2 seconds. Status values: `pending` → `working` → `finished` | `error` | `cancelled`

When done:
```json
{"status": "finished", "result": {"result": true}}
```

---

## 3. Drive to Coordinates (AGV Only)

Moves **only the AGV** to coordinates. No arm/lift movement. No automatic preparation.

### HTTP

```bash
curl -X POST https://api.techvisioncloud.pl/api/v1/symovo/go_to_pose \
  -H "Content-Type: application/json" \
  -d '{
    "x_m": 1.73,
    "y_m": -0.14,
    "theta_deg": 45.0,
    "map_id": 0,
    "wait": false
  }'
```

`wait: false` — returns immediately, AGV drives in background.
`wait: true` — blocks until AGV arrives (can take 30+ seconds).

### MQTT

```bash
mosquitto_pub -h 82.165.177.194 -p 8883 \
  --cafile ca.crt \
  -u <MQTT_USER> -P '<MQTT_PASSWORD>' \
  -t "aroc/robot/fahrdummy-01/commands/position" \
  -m '{
    "command_id": "pos-001",
    "x": 1.73,
    "y": -0.14,
    "theta": 45.0,
    "timestamp": "2026-03-18T12:00:00Z"
  }'
```

---

## 4. Stop & Emergency Stop

### Cancel current task (HTTP)

Stops all devices (AGV, arm, lift) in parallel.

```bash
curl -X POST https://api.techvisioncloud.pl/api/v1/robot/tasks/cancel_current
```

```json
{"status": "cancelled", "result": {"error": "CancelledError"}}
```

### Cancel by task ID (HTTP)

```bash
curl -X POST https://api.techvisioncloud.pl/api/v1/robot/tasks/cancel/t-87a3f2c1
```

### Cancel (MQTT)

```bash
mosquitto_pub -h 82.165.177.194 -p 8883 \
  --cafile ca.crt \
  -u <MQTT_USER> -P '<MQTT_PASSWORD>' \
  -t "aroc/robot/fahrdummy-01/commands/cancel" \
  -m '{
    "command_id": "cancel-001",
    "task_id": "t-87a3f2c1",
    "timestamp": "2026-03-18T12:00:00Z"
  }'
```

### Emergency Stop (HTTP)

**Immediately stops ALL devices** — AGV, arm, lift — in parallel. Use for safety.

```bash
curl -X POST https://api.techvisioncloud.pl/api/v1/robot/tasks/estop
```

```json
{"stopped": true, "cancelled_task": "t-87a3f2c1"}
```

### Emergency Stop (MQTT)

```bash
mosquitto_pub -h 82.165.177.194 -p 8883 \
  --cafile ca.crt \
  -u <MQTT_USER> -P '<MQTT_PASSWORD>' \
  -t "aroc/robot/fahrdummy-01/commands/estop" \
  -m '{
    "command_id": "estop-001",
    "timestamp": "2026-03-18T12:00:00Z",
    "reason": "User pressed emergency stop"
  }'
```

The MQTT bridge retries estop up to 6 times to guarantee delivery.

### Safety Recovery (after E-Stop)

After emergency stop, call this to re-enable all devices:

```bash
curl -X POST https://api.techvisioncloud.pl/api/v1/robot/safety/recover
```

```json
{
  "success": true,
  "steps": [
    {"name": "safety_relay_check", "status": "ok"},
    {"name": "igus_fault_reset", "status": "ok"},
    {"name": "xarm_recover", "status": "ok"},
    {"name": "drive_mode_enable", "status": "ok"}
  ]
}
```

---

## 5. Charging

### List charging stations

```bash
curl https://api.techvisioncloud.pl/api/v1/symovo/charging_stations
```

```json
{
  "stations": [
    {
      "id": 687327357713408,
      "name": "CHARGER",
      "state": "OK",
      "has_charger": true,
      "pose": {"x": -0.83, "y": -0.61, "theta": 2.24, "map_id": 0}
    }
  ]
}
```

- `state: "OK"` — station active (robot will return to it after navigation!)
- `state: "INACTIVE"` — station deactivated (safe to navigate away)

### Send robot to charger

```bash
curl -X POST https://api.techvisioncloud.pl/api/v1/robot/tasks/go_to_charging_station \
  -H "Content-Type: application/json" \
  -d '{"station_id": 687327357713408}'
```

The robot will stow the arm, lower the lift, activate the station, and dock automatically.

### Deactivate all charging stations

Done automatically before "move to product", but you can call manually:

```bash
curl -X POST https://api.techvisioncloud.pl/api/v1/symovo/charging_stations/disable_all
```

```json
{"deactivated": 1, "all_inactive": true}
```

---

## 6. System Status

### Full robot status

```bash
curl https://api.techvisioncloud.pl/api/v1/robot/status
```

```json
{
  "ready": true,
  "message": "",
  "igus": {
    "connected": true,
    "homed": true,
    "is_moving": false,
    "position_cm": 40.0
  },
  "symovo": {
    "online": true,
    "pose": {"x_m": 1.73, "y_m": -0.14, "theta_deg": 45.0, "map_id": 0},
    "velocity": {"vx_m_s": 0.0, "vy_m_s": 0.0, "omega_deg_s": 0.0},
    "battery_level_percent": 98.5,
    "state": "localization",
    "state_flags": {"drive_ready": false, "emergency_stop_reset_request": false}
  },
  "xarm": {}
}
```

### AGV pose only

```bash
curl https://api.techvisioncloud.pl/api/v1/symovo/pose
```

```json
{
  "online": true,
  "pose": {"x_m": 1.73, "y_m": -0.14, "theta_deg": 45.0, "map_id": 0}
}
```

### AGV full status

```bash
curl https://api.techvisioncloud.pl/api/v1/symovo/status
```

### Health checks

```bash
curl https://api.techvisioncloud.pl/api/v1/robot/healthz       # robot-service liveness
curl https://api.techvisioncloud.pl/api/v1/symovo/livez         # nav2adapter liveness
curl https://api.techvisioncloud.pl/api/v1/symovo/readyz        # nav2adapter readiness
```

---

## 7. Stow Robot (Transport Position)

Stows the arm and lowers the lift for safe transport (no AGV movement):

```bash
curl -X POST https://api.techvisioncloud.pl/api/v1/robot/move/to_transport_position \
  -H "Content-Type: application/json" \
  -d '{"velocity_percent": 25}'
```

---

## 8. MQTT Topics Reference

Broker: `82.165.177.194:8883` (TLS). All topics start with `aroc/robot/fahrdummy-01/`.

### Commands (you send)

| Full Topic | Payload | What it does |
|------------|---------|--------------|
| `aroc/robot/fahrdummy-01/commands/navigateTo` | `{"command_id": "...", "target_id": "91ec630e"}` | Move robot to saved waypoint (full orchestration) |
| `aroc/robot/fahrdummy-01/commands/position` | `{"command_id": "...", "x": 1.73, "y": -0.14, "theta": 45}` | Drive AGV to coordinates (AGV only) |
| `aroc/robot/fahrdummy-01/commands/cancel` | `{"command_id": "...", "task_id": "t-87a3f2c1"}` | Cancel running task |
| `aroc/robot/fahrdummy-01/commands/estop` | `{"command_id": "...", "reason": "..."}` | Emergency stop all devices |

### Responses (you subscribe)

| Full Topic | What you get |
|------------|--------------|
| `aroc/robot/fahrdummy-01/resp/robot` | ACK + result for navigateTo, cancel, estop |
| `aroc/robot/fahrdummy-01/resp/symovo` | ACK + result for position command |
| `aroc/robot/fahrdummy-01/status/navigation` | Navigation state changes |
| `aroc/robot/fahrdummy-01/status/system` | Heartbeat + active tasks |
| `aroc/robot/fahrdummy-01/telemetry` | Periodic: pose, battery, component states |

### Subscribe to all responses

```bash
mosquitto_sub -h 82.165.177.194 -p 8883 \
  --cafile ca.crt \
  -u <MQTT_USER> -P '<MQTT_PASSWORD>' \
  -t "aroc/robot/fahrdummy-01/resp/+" \
  -t "aroc/robot/fahrdummy-01/status/+" \
  -t "aroc/robot/fahrdummy-01/telemetry" \
  -v
```

JavaScript (MQTT.js):
```js
client.subscribe('aroc/robot/fahrdummy-01/resp/+');
client.subscribe('aroc/robot/fahrdummy-01/status/+');
client.subscribe('aroc/robot/fahrdummy-01/telemetry');
```

### MQTT Response format

Immediate ACK:
```json
{
  "type": "ack",
  "request_id": "nav-001",
  "service": "robot",
  "success": true,
  "status_code": 201,
  "body": {"success": true, "task_id": "t-87a3f2c1"},
  "command_name": "navigateTo",
  "error": null
}
```

When task finishes:
```json
{
  "type": "result",
  "request_id": "nav-001",
  "task_id": "t-87a3f2c1",
  "success": true,
  "body": {"status": "finished", "result": true}
}
```

Error:
```json
{
  "type": "ack",
  "request_id": "nav-001",
  "success": false,
  "error": {"type": "http_error", "message": "Device busy"}
}
```

---

## 9. Typical Frontend Flows

### Navigate to waypoint

```
1. GET  https://api.techvisioncloud.pl/api/v1/robot/waypoints/list               → get waypoint list
2. POST https://api.techvisioncloud.pl/api/v1/robot/waypoints/run?position_id=ID  → start (returns task_id)
3. GET  https://api.techvisioncloud.pl/api/v1/robot/tasks/status/{task_id}        → poll every 1-2s
4. When status = "finished" → done
```

### Record new waypoint

```
1. (User drives robot to position via joystick)
2. POST https://api.techvisioncloud.pl/api/v1/robot/waypoints/record              → save current state
3. GET  https://api.techvisioncloud.pl/api/v1/robot/waypoints/list                → refresh list
```

### Emergency stop

```
1. POST https://api.techvisioncloud.pl/api/v1/robot/tasks/estop                   → immediate stop
2. (Operator clears situation)
3. POST https://api.techvisioncloud.pl/api/v1/robot/safety/recover                → re-enable devices
```

### Send to charger

```
1. GET  https://api.techvisioncloud.pl/api/v1/symovo/charging_stations             → get station ID
2. POST https://api.techvisioncloud.pl/api/v1/robot/tasks/go_to_charging_station  → send to charger
```

---

## 10. Error Codes

| HTTP Code | Meaning | What to do |
|-----------|---------|------------|
| 200 | OK | |
| 201 | Task created (runs in background) | Poll `/tasks/status/{id}` |
| 202 | Device busy — task already running | Wait or cancel first |
| 404 | Waypoint or task not found | Check ID |
| 409 | Device error (arm fault, etc.) | Call `/safety/recover` |
| 423 | Safety lockout | Call `/safety/recover` |
| 429 | Rate limit (joystick) | Slow down |
| 503 | Device offline | Check hardware |

### Common Problems

| Problem | Fix |
|---------|-----|
| Robot drives to target then returns to charger | `curl -X POST https://api.techvisioncloud.pl/api/v1/symovo/charging_stations/disable_all` |
| 423 on any command | `curl -X POST https://api.techvisioncloud.pl/api/v1/robot/safety/recover` |
| Task stuck in "working" | `curl -X POST https://api.techvisioncloud.pl/api/v1/robot/tasks/cancel_current` |
| Robot not ready | `curl https://api.techvisioncloud.pl/api/v1/robot/status` — check which device has error |
