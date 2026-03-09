# Configuration Reference

This document describes all environment variables used across the robot project.
All services read configuration from environment variables, with sane defaults for the LAN deployment.

## Quick Start

```bash
cp .env.example .env    # edit with your actual values
docker compose up -d    # start all services
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  LAN  192.168.1.0/24                                                │
│                                                                     │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐   │
│  │ Host PC  │  │  xArm    │  │  igus    │  │  Depth Camera    │   │
│  │ .10      │  │  .220    │  │  .230    │  │  .55             │   │
│  └────┬─────┘  └──────────┘  └──────────┘  └──────────────────┘   │
│       │                                                             │
│  ┌────┴──────────────────────────────────────────────┐             │
│  │  Docker Services (on Host .10)                     │             │
│  │                                                    │             │
│  │  api-gateway     :8201   ← reverse proxy           │             │
│  │  robot-service   :8110   ← orchestrator            │             │
│  │  igus-service    :8101   ← lift motor control      │             │
│  │  xarm-service    :8102   ← arm control             │             │
│  │  symovo-service  :7905   ← AGV navigation          │             │
│  │  mqtt-service    :7900   ← AE.HUB bridge           │             │
│  │  frontend        :8401   ← web UI                  │             │
│  └───────────────────────────────────────────────────┘             │
│                                                                     │
│  External: MQTT broker / TURN server  82.165.177.194               │
└─────────────────────────────────────────────────────────────────────┘
```

## Environment Variables

### Network Topology

| Variable | Default | Description |
|---|---|---|
| `HOST_LAN_IP` | `192.168.1.10` | IP of the host machine on the robot LAN |

### Physical Devices

| Variable | Default | Description |
|---|---|---|
| `XARM_HARDWARE_IP` | `192.168.1.220` | xArm controller IP |
| `IGUS_MOTOR_IP` | `192.168.1.230` | igus dryve D1 motor IP |
| `IGUS_MOTOR_PORT` | `502` | Modbus/TCP port |
| `DEPTH_CAMERA_IP` | `192.168.1.55` | Intel RealSense depth camera IP |
| `SYMOVO_CAR_IP` | `192.168.1.100` | Symovo AGV IP |

### Service Ports

| Variable | Default | Used By |
|---|---|---|
| `API_GATEWAY_PORT` | `8201` | api-gateway |
| `IGUS_PORT` | `8101` | igus-service |
| `XARM_PORT` | `8102` | xarm-service |
| `ROBOT_PORT` | `8110` | robot-service |
| `SYMOVO_PORT` | `7905` | symovo-service (nav2adapter) |
| `TELEOP_PORT` | `7906` | symovo-service teleop server |
| `MQTT_API_PORT` | `7900` | mqtt-service HTTP API |
| `COLOR_CAMERA_PORT` | `8900` | color camera Janus |
| `FRONTEND_PORT` | `8401` | frontend |

### MQTT / AE.HUB

| Variable | Default | Description |
|---|---|---|
| `MQTT_BROKER` | _(empty)_ | MQTT broker hostname/IP |
| `MQTT_PORT` | `1883` | MQTT broker port (8883 for TLS) |
| `MQTT_USER` | _(empty)_ | MQTT username |
| `MQTT_PASS` | _(empty)_ | MQTT password (secret) |
| `MQTT_USE_TLS` | `false` | Enable TLS for MQTT |
| `MQTT_TLS_INSECURE` | `false` | Skip TLS certificate verification |
| `ROBOT_ID` | `robot-01` | Robot identifier for MQTT topics |

### WebRTC / TURN

| Variable | Default | Description |
|---|---|---|
| `TURN_HOST` | `82.165.177.194` | TURN server hostname |
| `TURN_PORT` | `3478` | TURN server port |
| `TURN_USER` | _(empty)_ | TURN username |
| `TURN_CREDENTIAL` | _(empty)_ | TURN credential (secret) |

### xArm WebSocket

| Variable | Default | Description |
|---|---|---|
| `XARM_WS_PORT` | `18333` | xArm WebSocket port |
| `XARM_WS_URL` | auto-derived | Full WebSocket URL override |

### Service URL Overrides

These override the auto-derived `http://<HOST_LAN_IP>:<PORT>` URLs:

| Variable | Used By |
|---|---|
| `IGUS_SERVICE_URL` | mqtt-service, api-gateway |
| `XARM_SERVICE_URL` | mqtt-service, api-gateway |
| `ROBOT_SERVICE_URL` | mqtt-service, api-gateway |
| `SYMOVO_SERVICE_URL` | mqtt-service, api-gateway |
| `API_GATEWAY_URL` | frontend |

### Logging

| Variable | Default | Description |
|---|---|---|
| `LOG_LEVEL` | `INFO` | Log level (DEBUG, INFO, WARNING, ERROR) |
| `JSON_LOGS` | `false` | Output structured JSON logs |
| `APP_ENV` | `production` | Environment name (dev/staging/production) |

## Shared Config Package

The `shared_config/` package at the project root provides:

- **`shared_config.network.PORTS`** — canonical port numbers (`ServicePorts` dataclass)
- **`shared_config.network.DEVICES`** — default device IPs (`DeviceDefaults` dataclass)
- **`shared_config.network.get_service_url(name)`** — resolves a service URL from env vars
- **`shared_config.settings_base.BaseServiceSettings`** — base pydantic-settings class

All constants are overridable via environment variables. The defaults match the current LAN topology.

## Docker Compose

| File | Purpose |
|---|---|
| `docker-compose.yml` | Full stack orchestration |
| `docker-compose.prod.yml` | Production security hardening overlay |
| `<service>/docker-compose.yml` | Standalone dev/debug for individual services |

### Starting the full stack

```bash
# Development
docker compose up -d

# Production (with security hardening)
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# Single service (standalone)
cd igus_service && docker compose up -d
```

### Notes

- **igus-service** uses `network_mode: host` because Modbus/TCP requires direct LAN access to the motor controller.
- All other services communicate via the `robot-net` bridge network.
- The root `.env` file is loaded by all services via `env_file: .env` in compose.
