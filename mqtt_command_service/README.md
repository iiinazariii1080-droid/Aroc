# MQTT Command Service

Professional MQTT-to-HTTP bridge for robot management through a cloud platform. The service provides bidirectional communication between the platform and robot via MQTT protocol, automatic reconnection, TLS encryption, and REST API for configuration management.

## Quick Start

### Requirements

- Python 3.11+
- Access to MQTT broker
- Access to robot HTTP services (optional)

### Installation

```bash
# Clone repository
git clone <repository-url>
cd mqtt_command_service

# Install dependencies
pip install -r requirements.txt
```

### Minimal Configuration

Create a `.env` file in the project root:

```bash
# MQTT Broker (required)
MQTT_BROKER=your-broker-ip
MQTT_PORT=1883                      # 1883 for MQTT, 8883 for MQTTS (auto-switches with TLS)
MQTT_USER=bridge_user
MQTT_PASS=your_password

# TLS (recommended for production)
MQTT_USE_TLS=true                   # Enable TLS
MQTT_CA_CERTS=/app/certs/ca.crt     # Path to CA certificate
MQTT_TLS_INSECURE=false             # Certificate validation (false for production)

# Robot Identity (required)
ROBOT_ID=fahrdummy-01
```

### Running

```bash
# Run all services (bridge + telemetry)
python main.py

# Or bridge only
python -c "from bridge import MqttCommandBridge; from config import load_bridge_config; bridge = MqttCommandBridge(load_bridge_config()); bridge.run_forever()"

# Or telemetry only
python telemetry.py
```

## 📋 Service Components

The service consists of three main components:

### 1. MQTT Bridge (`bridge.py`)
- Routing MQTT commands → HTTP requests
- Handling navigation commands (navigateTo, cancel, estop, driveToPosition)
- Monitoring long-running tasks
- Automatic reconnection (up to 3 attempts)
- Publishing statuses (system, connection, navigation)

### 2. Telemetry Service (`telemetry.py`)
- Collecting robot telemetry
- Publishing statuses (navigation, system, connection, telemetry)
- Monitoring WebRTC connections
- Automatic reconnection with exponential backoff

### 3. REST API (`app/main.py`)
- MQTT broker configuration management
- TLS certificate upload
- API key management
- Health check and metrics

## ⚙️ Configuration

### Environment Variables

#### MQTT Broker (required)

```bash
MQTT_BROKER=your-broker-ip          # MQTT broker address
MQTT_PORT=1883                      # Port (1883 for MQTT, 8883 for MQTT over TLS)
MQTT_USER=bridge_user               # Username
MQTT_PASS=your_password             # Password
MQTT_CLIENT_ID=fahrdummy-01         # Client ID (auto-generated if not specified)
```

**Important:** When TLS is enabled (`MQTT_USE_TLS=true`), the port automatically switches to 8883.

#### TLS/SSL (optional)

```bash
MQTT_USE_TLS=true                   # Enable TLS (default: false)
MQTT_CA_CERTS=/app/certs/ca.crt     # Path to CA certificate (in container: /app/certs/ca.crt)
MQTT_CERTFILE=/app/certs/client.crt # Path to client certificate (for mutual TLS)
MQTT_KEYFILE=/app/certs/client.key  # Path to private key (for mutual TLS)
MQTT_TLS_INSECURE=false             # Disable certificate validation (ONLY for dev/testing)
```

**Automatic port switching:**
- If `MQTT_USE_TLS=true` and port = 1883 → automatically switches to 8883
- If `MQTT_USE_TLS=false` and port = 8883 → automatically switches to 1883

**Certificate storage:**
- In container: `/app/certs/` (mounted from `./certs` on host)
- Files: `ca.crt`, `client.crt`, `client.key`
- Can be uploaded via API: `POST /api/v1/config/certificates/upload`

#### Robot Identity

```bash
ROBOT_ID=fahrdummy-02               # Unique robot ID
```

#### Hub Authentication (optional)

```bash
HUB_BASE_URL=https://hub.example.com  # Hub API URL
HUB_ROBOT_ID=fahrdummy-02             # Robot ID in Hub
HUB_API_KEY=your_api_key              # API key for obtaining JWT tokens
HUB_AUTH_REFRESH_MARGIN=60.0          # Token refresh margin (seconds)
```

#### Service Configuration (optional)

```bash
SERVICE_USE_LOCAL=0                  # 1 for local services, 0 for remote
SERVICE_MAP_JSON='{"robot":{"base_url":"http://localhost:8110"}}'  # Service URL override
```

#### Timeouts and Intervals

```bash
HTTP_TIMEOUT=5.0                     # HTTP request timeout (seconds)
TASK_POLL_INTERVAL=1.0               # Task polling interval (seconds)
TASK_POLL_TIMEOUT=300.0              # Task polling timeout (seconds)
STATUS_HEARTBEAT_INTERVAL=15.0       # Status heartbeat interval (seconds)
MQTT_PUBLISH_QOS=1                   # QoS level for MQTT messages (0, 1, 2)
```

#### REST API (optional)

```bash
API_ENABLED=false                    # Enable REST API (default: false)
API_HOST=0.0.0.0                    # API server host
API_PORT=7900                       # API server port
```

#### Telemetry Service

```bash
POLL_INTERVAL_SECONDS=5              # Service polling interval (seconds)
HTTP_TIMEOUT_SECONDS=2               # HTTP request timeout for telemetry
IS_REMOTE=true                       # Use remote services
LOCAL_IP=192.168.1.10               # Local IP for services
REMOTE_ADDRESS=api.techvisioncloud.pl/api/v1  # Remote API address
```

### Configuration Storage

Configuration is stored with priority:
1. **Database** (`config.db`) - for dynamic settings (MQTT_BROKER, MQTT_PORT, TLS settings)
2. **Environment variables** (`.env` file or system variables)
3. **Default values**

On first run, values from `.env` are automatically migrated to the database.

## 🔌 Using MQTT

### Topic Structure

All topics follow the pattern: `aroc/robot/{ROBOT_ID}/{category}/{subcategory}`

### Commands (Platform → Robot)

#### 1. HTTP Proxy Commands

Sending commands to robot services via MQTT:

**Topic:** `aroc/robot/{ROBOT_ID}/cmd/{SERVICE}`

**Available services:**
- `igus` - Igus lift
- `xarm` - XArm manipulator
- `symovo` - Symovo drive
- `robot` - Main robot service
- `mqtt` - API service (port 7900)

**Example: Get Igus status**
```json
{
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "method": "GET",
  "path": "/status"
}
```

**Example: Move Igus lift**
```json
{
  "request_id": "move-001",
  "method": "POST",
  "path": "/move",
  "body": {
    "position": 30000,
    "velocity_percent": 25,
    "acceleration_percent": 25
  },
  "timeout": 30.0
}
```

**Example: Call API endpoint via mqtt service**
```json
{
  "request_id": "api-001",
  "method": "GET",
  "path": "/api/v1/config/broker",
  "headers": {
    "X-API-Key": "your-api-key"
  }
}
```

**Fields:**
- `request_id` (required): Unique request identifier
- `method` (required): HTTP method (GET, POST, PUT, DELETE)
- `path` (required): API endpoint path
- `body` (optional): Request body (JSON object)
- `headers` (optional): Custom HTTP headers
- `timeout` (optional): Request timeout in seconds

#### 2. Navigation Commands (two levels)

**Robot-level (full orchestration — AGV + lift + arm):**

**Topic:** `aroc/robot/{ROBOT_ID}/commands/navigateTo`
- Routes to `robot_service:/tasks/navigate`
- Resolves `target_id` from robot_service waypoints DB
- `target_id` values: `GET /api/v1/robot/waypoints/list` (e.g. "Elmex", "Aloe Vera")

```json
{
  "command_id": "nav-001",
  "target_id": "Elmex",
  "priority": "normal",
  "timestamp": "2025-01-27T12:00:00Z",
  "metadata": {
    "user": "operator_1",
    "reason": "Pick up product"
  },
  "headers": {}
}
```

**Device-level (AGV base only):**

**Topic:** `aroc/robot/{ROBOT_ID}/commands/driveToPosition`
- Handled directly by nav2adapter (not routed via bridge)
- Resolves `target_id` from nav2adapter positions DB
- `target_id` values: `GET /api/v1/symovo/robot_positions/list` (e.g. "POSITION_1")

```json
{
  "command_id": "drive-001",
  "target_id": "POSITION_1",
  "timestamp": "2025-01-27T12:00:00Z"
}
```

**Topic:** `aroc/robot/{ROBOT_ID}/commands/cancel`

```json
{
  "command_id": "cancel-001",
  "task_id": "task-123",
  "timestamp": "2025-01-27T12:00:00Z",
  "reason": "User cancelled",
  "headers": {}
}
```

**Topic:** `aroc/robot/{ROBOT_ID}/commands/estop`

```json
{
  "command_id": "estop-001",
  "timestamp": "2025-01-27T12:00:00Z",
  "reason": "Emergency stop button pressed",
  "headers": {}
}
```

### Responses (Robot → Platform)

**Topic:** `aroc/robot/{ROBOT_ID}/resp/{SERVICE}`

**ACK Response (immediate response):**
```json
{
  "type": "ack",
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "service": "igus",
  "success": true,
  "status_code": 200,
  "headers": {
    "Content-Type": "application/json"
  },
  "body": {
    "success": true,
    "position": 30000
  },
  "error": null
}
```

**Result Response (after task completion):**
```json
{
  "type": "result",
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "task_id": "task-123",
  "service": "robot",
  "success": true,
  "status_code": 200,
  "body": {
    "status": "finished",
    "result": {...}
  },
  "error": null
}
```

**Error Response:**
```json
{
  "type": "ack",
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "service": "igus",
  "success": false,
  "status_code": 0,
  "body": null,
  "error": {
    "type": "http_error",
    "message": "Connection timeout"
  }
}
```

### Statuses (Robot → Platform)

#### System Status

**Topic:** `aroc/robot/{ROBOT_ID}/status/system`

```json
{
  "type": "status",
  "status_type": "system",
  "robot_id": "fahrdummy-02",
  "timestamp": "2025-01-27T12:00:00Z",
  "uptime_seconds": 3600,
  "bridge": {
    "active_tasks": {
      "count": 2,
      "ids": ["task-123", "task-456"]
    }
  }
}
```

#### Connection Status

**Topic:** `aroc/robot/{ROBOT_ID}/status/connection`

```json
{
  "type": "status",
  "status_type": "connection",
  "robot_id": "fahrdummy-01",
  "timestamp": "2025-01-27T12:00:00Z",
  "mqtt": {
    "connected": true,
    "client_id": "fahrdummy-01",
    "MQTT_BROKER": {
      "host": "your-broker-ip",
      "port": 1883
    },
    "last_disconnect_ts": null
  },
  "auth": {
    "enabled": true,
    "token_active": true
  }
}
```

#### Navigation Status

**Topic:** `aroc/robot/{ROBOT_ID}/status/navigation`

```json
{
  "type": "status",
  "status_type": "navigation",
  "robot_id": "fahrdummy-02",
  "timestamp": "2025-01-27T12:00:00Z",
  "command_id": "nav-001",
  "command_name": "navigateTo",
  "target_id": "position_A",
  "task_id": "task-123",
  "state": "completed",
  "success": true,
  "detail": {...}
}
```

**Possible states:**
- `acknowledged` - Command received and accepted
- `duplicate` - Command with this ID already processed (idempotency)
- `completed` - Task completed successfully
- `failed` - Task failed with error
- `timeout` - Task polling timeout
- `rejected` - Command rejected (validation error)

#### Telemetry

**Topic:** `aroc/robot/{ROBOT_ID}/telemetry`

```json
{
  "robot_id": "fahrdummy-02",
  "timestamp": "2025-01-27T12:00:00Z",
  "data": {
    "pose": {
      "x": 5.2,
      "y": 3.1,
      "theta": 0.785,
      "map_id": 1
    },
    "velocity": {
      "vx": 0.5,
      "vy": 0.0,
      "omega": 0.1
    },
    "battery_percent": 85.5,
    "state": "navigating",
    "state_flags": {...}
  },
  "components": {
    "igus": {
      "connected": true,
      "homed": true,
      "is_moving": false,
      "position_cm": 150.0
    },
    "xarm": {
      "connected": true,
      "has_error": false,
      "state_code": 3
    }
  }
}
```

##Using REST API

REST API is available on port 7900 (default) and provides configuration management without requiring direct file access.

### Enabling API

```bash
export API_ENABLED=true
python main.py
```

API will be available at: `http://localhost:7900`

### Authentication

All endpoints (except `/health`, `/`, `/metrics`) require an API key in the header:

```bash
curl -H "X-API-Key: your-api-key" http://localhost:7900/api/v1/config/broker
```

### Creating API Key

**Required:** ADMIN role (first key is created via script)

```bash
python scripts/create_first_api_key.py
```

Or via API (if you already have an ADMIN key):

```bash
curl -X POST "http://localhost:7900/api/v1/auth/api-keys" \
  -H "X-API-Key: your-admin-key" \
  -F "role=write" \
  -F "description=Configuration management"
```

**Emergency API Key:**
For emergency access, a hardcoded API key is always available:
- **Key:** `<your-emergency-api-key>`
- **Role:** ADMIN (full access)
- **Note:** This key works even if database is unavailable

**Roles:**
- `read` - Read-only access to configuration
- `write` - Read and modify configuration
- `admin` - Full access, including API key management

### Main Endpoints

#### 1. Health Check

```bash
curl http://localhost:7900/health
```

**Response:**
```json
{
  "status": "healthy",
  "database": "ok",
  "timestamp": "2025-01-27T12:00:00Z"
}
```

#### 2. Get Broker Configuration

```bash
curl -H "X-API-Key: your-key" \
  http://localhost:7900/api/v1/config/broker
```

#### 3. Update Broker Configuration

```bash
curl -X POST "http://localhost:7900/api/v1/config/broker" \
  -H "X-API-Key: your-write-key" \
  -H "Content-Type: application/json" \
  -d '{
    "MQTT_BROKER": "123.123.123.123",
    "MQTT_PORT": 8883,
    "mqtt_user": "new_user",
    "mqtt_password": "new_password"
  }'
```

**Important:** Before applying changes, a connection test to the new broker is performed.

#### 4. Upload TLS Certificate

```bash
curl -X POST "http://localhost:7900/api/v1/config/certificates/upload" \
  -H "X-API-Key: your-write-key" \
  -F "cert_type=ca_cert" \
  -F "file=@/path/to/ca.crt" \
  -F "auto_update_config=true"
```

**Certificate types:**
- `ca_cert` - CA certificate for broker certificate validation
- `client_cert` - Client certificate (for mutual TLS)
- `client_key` - Private key (for mutual TLS)

#### 5. Upload Multiple Certificates

```bash
curl -X POST "http://localhost:7900/api/v1/config/certificates/upload-multiple" \
  -H "X-API-Key: your-write-key" \
  -F "ca_cert=@/path/to/ca.crt" \
  -F "client_cert=@/path/to/client.crt" \
  -F "client_key=@/path/to/client.key" \
  -F "auto_update_config=true"
```

#### 6. Test Broker Connection

```bash
curl -X POST "http://localhost:7900/api/v1/config/broker/test-connection" \
  -H "X-API-Key: your-write-key" \
  -H "Content-Type: application/json" \
  -d '{
    "MQTT_BROKER": "123.123.123.123",
    "MQTT_PORT": 8883,
    "mqtt_user": "test_user",
    "mqtt_password": "test_password"
  }'
```

### Swagger Documentation

After starting the API, interactive documentation is available:

- **Swagger UI:** http://localhost:7900/docs
- **ReDoc:** http://localhost:7900/redoc
- **OpenAPI JSON:** http://localhost:7900/openapi.json

## Using Ports and TLS Certificates

### Port 1883 (MQTT, without TLS)

**Warning:** Port 1883 is not encrypted. Use only for:
- Testing in isolated networks
- Development
- Internal networks with additional protection (VPN, firewall)

**Usage example:**
```bash
# Send command
mosquitto_pub -h your-broker-ip -p 1883 \
  -u bridge_user -P 'your_password' \
  -t "aroc/robot/fahrdummy-01/cmd/igus" \
  -m '{"request_id": "test-123", "method": "GET", "path": "/status"}'

# Subscribe to responses
mosquitto_sub -h your-broker-ip -p 1883 \
  -u bridge_user -P 'your_password' \
  -t "aroc/robot/fahrdummy-01/resp/igus"
```

### Port 8883 (MQTTS, with TLS) Recommended

**Using TLS provides:**
- Encryption of all data
- Protection against command interception
- Server authentication
- Password protection

**Requirements:**
- CA certificate (required)
- Client certificate (optional, for mutual TLS)

### Obtaining and Using Certificates

#### 1. Get CA Certificate from Broker Administrator

CA certificate is usually provided by the MQTT broker administrator. Save it to a file:

```bash
# Save CA certificate
cat > ca.crt << 'EOF'
-----BEGIN CERTIFICATE-----
... (CA certificate content)
-----END CERTIFICATE-----
EOF
```

#### 2. Using with mosquitto_pub/mosquitto_sub

**Basic usage (CA certificate only):**
```bash
# Send command
mosquitto_pub -h your-broker-ip -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'your_password' \
  -t "aroc/robot/fahrdummy-01/cmd/igus" \
  -m '{"request_id": "test-123", "method": "GET", "path": "/status"}'

# Subscribe
mosquitto_sub -h your-broker-ip -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'your_password' \
  -t "aroc/robot/fahrdummy-01/resp/igus"
```

**With client certificate (mutual TLS):**
```bash
# Send command
mosquitto_pub -h your-broker-ip -p 8883 \
  --cafile /path/to/ca.crt \
  --cert /path/to/client.crt \
  --key /path/to/client.key \
  -u bridge_user -P 'your_password' \
  -t "aroc/robot/fahrdummy-01/cmd/igus" \
  -m '{"request_id": "test-123", "method": "GET", "path": "/status"}'

# Subscribe
mosquitto_sub -h your-broker-ip -p 8883 \
  --cafile /path/to/ca.crt \
  --cert /path/to/client.crt \
  --key /path/to/client.key \
  -u bridge_user -P 'your_password' \
  -t "aroc/robot/fahrdummy-01/resp/igus"
```

**With disabled certificate validation (testing only!):**
```bash
# ⚠️ DO NOT use in production!
mosquitto_pub -h your-broker-ip -p 8883 \
  --cafile /path/to/ca.crt \
  --insecure \
  -u bridge_user -P 'your_password' \
  -t "aroc/robot/fahrdummy-01/cmd/igus" \
  -m '{"request_id": "test-123", "method": "GET", "path": "/status"}'
```

#### 3. Uploading Certificates to Service

**Via REST API:**
```bash
# Upload CA certificate
curl -X POST "http://localhost:7900/api/v1/config/certificates/upload" \
  -H "X-API-Key: your-api-key" \
  -F "cert_type=ca_cert" \
  -F "cert_file=@ca.crt"

# Upload all certificates at once
curl -X POST "http://localhost:7900/api/v1/config/certificates/upload-multiple" \
  -H "X-API-Key: your-api-key" \
  -F "ca_cert=@ca.crt" \
  -F "client_cert=@client.crt" \
  -F "client_key=@client.key"
```

**Via Docker volume:**
```bash
# Copy certificates to certs/ directory
cp ca.crt ./certs/
cp client.crt ./certs/  # if using mutual TLS
cp client.key ./certs/  # if using mutual TLS

# Restart container
docker compose restart
```

**In container, certificates should be in:**
- `/app/certs/ca.crt` - CA certificate
- `/app/certs/client.crt` - Client certificate (optional)
- `/app/certs/client.key` - Private key (optional)

#### 4. Configuring Service for TLS

**Via environment variables (.env):**
```bash
MQTT_USE_TLS=true
MQTT_CA_CERTS=/app/certs/ca.crt
MQTT_CERTFILE=/app/certs/client.crt  # optional
MQTT_KEYFILE=/app/certs/client.key    # optional
MQTT_TLS_INSECURE=false               # true only for testing
```

**Via REST API:**
```bash
# Enable TLS
curl -X POST "http://localhost:7900/api/v1/config/broker" \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"mqtt_use_tls": true}'
```

**Emergency API Key:**
For emergency access, a hardcoded API key is always available:
- **Key:** `<your-emergency-api-key>`
- **Role:** ADMIN (full access)
- **Note:** This key works even if database is unavailable

**Automatic port switching:**
- When TLS is enabled, port automatically switches from 1883 to 8883
- When TLS is disabled, port automatically switches from 8883 to 1883

### Testing TLS Connection

```bash
# Connection test (should return success)
mosquitto_pub -h your-broker-ip -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'your_password' \
  -t "test/connection" \
  -m "test" \
  -q 1

# If error - check:
# 1. Correct path to CA certificate
# 2. Certificate validity
# 3. Broker settings (port 8883 must be open)
```

## Usage Examples

### Example 1: Sending Command via MQTT (without TLS, port 1883)

**Warning:** Port 1883 is not protected! Use only for testing in a secure network.

**Send command:**
```bash
mosquitto_pub -h your-broker-ip -p 1883 \
  -u bridge_user -P 'your_password' \
  -t "aroc/robot/fahrdummy-01/cmd/igus" \
  -m '{
    "request_id": "test-123",
    "method": "GET",
    "path": "/status"
  }'
```

**Subscribe to responses:**
```bash
mosquitto_sub -h your-broker-ip -p 1883 \
  -u bridge_user -P 'your_password' \
  -t "aroc/robot/fahrdummy-01/resp/igus"
```

### Example 1.1: Sending Command via MQTT with TLS (port 8883) ✅ Recommended

**Send command with TLS:**
```bash
mosquitto_pub -h your-broker-ip -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'your_password' \
  -t "aroc/robot/fahrdummy-01/cmd/igus" \
  -m '{
    "request_id": "test-123",
    "method": "GET",
    "path": "/status"
  }'
```

**Subscribe to responses with TLS:**
```bash
mosquitto_sub -h your-broker-ip -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'your_password' \
  -t "aroc/robot/fahrdummy-01/resp/igus"
```

**With disabled certificate validation (testing only):**
```bash
mosquitto_pub -h your-broker-ip -p 8883 \
  --cafile /path/to/ca.crt \
  --insecure \
  -u bridge_user -P 'your_password' \
  -t "aroc/robot/fahrdummy-01/cmd/igus" \
  -m '{"request_id": "test-123", "method": "GET", "path": "/status"}'
```

### Example 1.2: Using mqtt Service (API proxy)

**Call API endpoint via MQTT:**
```bash
mosquitto_pub -h your-broker-ip -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'your_password' \
  -t "aroc/robot/fahrdummy-01/cmd/mqtt" \
  -m '{
    "request_id": "api-001",
    "method": "GET",
    "path": "/api/v1/config/broker",
    "headers": {
      "X-API-Key": "your-api-key"
    }
  }'
```

**Subscribe to responses:**
```bash
mosquitto_sub -h your-broker-ip -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'your_password' \
  -t "aroc/robot/fahrdummy-01/resp/mqtt"
```

### Example 2: Python Client (without TLS)

```python
import paho.mqtt.client as mqtt
import json
import uuid
import time

def on_connect(client, userdata, flags, rc):
    print(f"Connected with result code {rc}")
    # Subscribe to responses and statuses
    client.subscribe("aroc/robot/fahrdummy-01/resp/+")
    client.subscribe("aroc/robot/fahrdummy-01/status/+")

def on_message(client, userdata, msg):
    payload = json.loads(msg.payload.decode())
    print(f"Topic: {msg.topic}")
    print(f"Payload: {json.dumps(payload, indent=2)}")

# Create client
client = mqtt.Client()
client.username_pw_set("bridge_user", "your_password")
client.on_connect = on_connect
client.on_message = on_message

# Connect (port 1883, without TLS)
client.connect("your-broker-ip", 1883, 60)
client.loop_start()

# Send command
request_id = str(uuid.uuid4())
command = {
    "request_id": request_id,
    "method": "GET",
    "path": "/status"
}
client.publish(
    "aroc/robot/fahrdummy-01/cmd/igus",
    json.dumps(command),
    qos=1
)

# Wait for responses
time.sleep(10)
client.loop_stop()
client.disconnect()
```

### Example 2.1: Python Client with TLS (recommended)

```python
import paho.mqtt.client as mqtt
import json
import uuid
import time

def on_connect(client, userdata, flags, rc):
    print(f"Connected with result code {rc}")
    client.subscribe("aroc/robot/fahrdummy-01/resp/+")
    client.subscribe("aroc/robot/fahrdummy-01/status/+")

def on_message(client, userdata, msg):
    payload = json.loads(msg.payload.decode())
    print(f"Topic: {msg.topic}")
    print(f"Payload: {json.dumps(payload, indent=2)}")

# Create client
client = mqtt.Client()
client.username_pw_set("bridge_user", "your_password")

# Configure TLS
client.tls_set(
    ca_certs="/path/to/ca.crt",      # CA certificate
    certfile="/path/to/client.crt",  # Client certificate (optional)
    keyfile="/path/to/client.key"    # Private key (optional)
)
# client.tls_insecure_set(True)  # Testing only!

client.on_connect = on_connect
client.on_message = on_message

# Connect (port 8883, with TLS)
client.connect("your-broker-ip", 8883, 60)
client.loop_start()

# Send command
request_id = str(uuid.uuid4())
command = {
    "request_id": request_id,
    "method": "GET",
    "path": "/status"
}
client.publish(
    "aroc/robot/fahrdummy-01/cmd/igus",
    json.dumps(command),
    qos=1
)

# Wait for responses
time.sleep(10)
client.loop_stop()
client.disconnect()
```

### Example 3: Navigation to Position (with TLS)

```bash
# Send navigation command
mosquitto_pub -h your-broker-ip -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'your_password' \
  -t "aroc/robot/fahrdummy-01/commands/navigateTo" \
  -m '{
    "command_id": "nav-001",
    "target_id": "position_A",
    "priority": "normal",
    "timestamp": "2025-01-27T12:00:00Z"
  }'

# Monitor navigation status
mosquitto_sub -h your-broker-ip -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'your_password' \
  -t "aroc/robot/fahrdummy-01/status/navigation"
```

### Example 4: Updating Configuration via API

```bash
# Get current configuration
curl -H "X-API-Key: your-key" \
  http://localhost:7900/api/v1/config/broker

# Update broker
curl -X POST "http://localhost:7900/api/v1/config/broker" \
  -H "X-API-Key: your-write-key" \
  -H "Content-Type: application/json" \
  -d '{
    "MQTT_BROKER": "new-broker.example.com",
    "MQTT_PORT": 8883,
    "mqtt_use_tls": true
  }'

# Upload TLS certificates
curl -X POST "http://localhost:7900/api/v1/config/certificates/upload-multiple" \
  -H "X-API-Key: your-write-key" \
  -F "ca_cert=@ca.crt" \
  -F "client_cert=@client.crt" \
  -F "client_key=@client.key"
```

### Example 5: Subscribing to Telemetry with TLS

```bash
# Subscribe to telemetry
mosquitto_sub -h your-broker-ip -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'your_password' \
  -t "aroc/robot/fahrdummy-01/telemetry" \
  -v  # -v to output topic and message

# Subscribe to all statuses
mosquitto_sub -h your-broker-ip -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'your_password' \
  -t "aroc/robot/fahrdummy-01/status/+" \
  -v

# Subscribe to all responses
mosquitto_sub -h your-broker-ip -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'your_password' \
  -t "aroc/robot/fahrdummy-01/resp/+" \
  -v
```

## 🐳 Docker

### Building Image

```bash
docker build -t mqtt-command-service .
```

### Running Container

```bash
docker run -d \
  --name mqtt-service \
  -p 7900:7900 \
  --env-file .env \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/certs:/app/certs \
  mqtt-command-service
```

### Docker Compose

Use the provided `docker-compose.yml`:

```bash
# Start
docker compose up -d

# View logs
docker compose logs -f

# Stop
docker compose down
```

## 🤖 Deployment on Robot

For deployment on a robot with Ubuntu, use the automatic installation script:

### Quick Installation

```bash
# 1. Copy project to robot
cd /opt
sudo git clone <repository-url> mqtt_command_service

# 2. Check system readiness
cd mqtt_command_service
sudo ./scripts/check_deployment.sh

# 3. Configure
sudo cp .env.example .env
sudo nano .env  # Fill with real values

# 4. Run installation
sudo ./scripts/deploy_to_robot.sh
```

### Service Management

```bash
# Status
sudo systemctl status mqtt-command-service

# Logs
sudo journalctl -u mqtt-command-service -f

# Restart
sudo systemctl restart mqtt-command-service
```

**Detailed documentation:** See `DEPLOYMENT.md` for full deployment guide.

## Troubleshooting

### Issue: Bridge Not Connecting to MQTT Broker

**Check:**
1. Verify credentials:
   ```bash
   echo $MQTT_BROKER
   echo $MQTT_USER
   echo $MQTT_PASS
   ```

2. Check broker availability:
   ```bash
   mosquitto_pub -h $MQTT_BROKER -p $MQTT_PORT \
     -u $MQTT_USER -P $MQTT_PASS \
     -t test -m "test"
   ```

3. Check logs:
   ```bash
   python main.py 2>&1 | grep -i "connect\|error"
   ```

**Solution:**
- Ensure broker is accessible from your network
- Verify credentials are correct
- For TLS: ensure certificates are uploaded and paths are correct

### Issue: Commands Not Executing

**Check:**
1. Verify subscription to topics:
   ```bash
   mosquitto_sub -h $MQTT_BROKER -p $MQTT_PORT \
     -u $MQTT_USER -P $MQTT_PASS \
     -t "aroc/robot/$ROBOT_ID/cmd/+"
   ```

2. Check HTTP service availability:
   ```bash
   curl http://localhost:8110/status  # for robot service
   ```

3. Check bridge logs:
   ```bash
   python main.py 2>&1 | grep -i "command\|error"
   ```

**Solution:**
- Ensure robot services are accessible
- Verify `request_id` in commands is correct
- Increase `HTTP_TIMEOUT` if commands are long-running

### Issue: No Status Messages

**Check:**
1. Ensure bridge is running and connected
2. Subscribe to status topics:
   ```bash
   mosquitto_sub -h $MQTT_BROKER -p $MQTT_PORT \
     -u $MQTT_USER -P $MQTT_PASS \
     -t "aroc/robot/$ROBOT_ID/status/#"
   ```

3. Check heartbeat interval:
   ```bash
   echo $STATUS_HEARTBEAT_INTERVAL
   ```

**Solution:**
- Ensure `STATUS_HEARTBEAT_INTERVAL > 0`
- Verify bridge successfully connected (see logs)

### Issue: TLS Errors

**Check:**
1. Verify certificates exist:
   ```bash
   ls -la $MQTT_CA_CERTS
   ls -la $MQTT_CERTFILE
   ls -la $MQTT_KEYFILE
   ```

2. Check permissions:
   ```bash
   chmod 644 $MQTT_CA_CERTS
   chmod 644 $MQTT_CERTFILE
   chmod 600 $MQTT_KEYFILE
   ```

**Solution:**
- Ensure certificate paths are correct
- Verify certificates are not expired
- For dev: can temporarily use `MQTT_TLS_INSECURE=true`

### Issue: API Not Responding

**Check:**
1. Ensure API is enabled:
   ```bash
   echo $API_ENABLED
   ```

2. Check health endpoint:
   ```bash
   curl http://localhost:7900/health
   ```

3. Check logs:
   ```bash
   python main.py 2>&1 | grep -i "api\|error"
   ```

**Solution:**
- Set `API_ENABLED=true`
- Verify port 7900 is not occupied by another process
- Check firewall rules

## Additional Documentation

- **MQTT Client Guide:** `README_CLIENT.md` - Complete guide for MQTT clients connecting to the broker
- **MQTT Protocol:** `docs/mqtt_contract.md` - Complete MQTT protocol specification
- **API Authentication:** `docs/API_AUTHENTICATION.md` - API authentication guide
- **Architecture:** `docs/ARCHITECTURE_ANALYSIS.md` - Architecture analysis
- **Technical Spec:** `docs/Technical_Specification_AROC_Connector_EN.md` - Technical specification
- **SRS:** `docs/SRS_AE_HUB_MVP_Draft_0_3_EN.md` - Software Requirements Specification

## Development

### Setting Up the Dev Environment

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate

pip install -r requirements.txt
```

### Code Quality Tooling

| Tool   | Purpose              | Command                          |
|--------|----------------------|----------------------------------|
| ruff   | Lint & format        | `ruff check .` / `ruff format .` |
| mypy   | Static type checking | `mypy --config-file mypy.ini bridge.py telemetry.py config.py config_storage.py healthcheck.py command_dedup.py env_settings.py bridge_protocol.py utils.py diagnostics.py app/` |
| pytest | Tests + coverage     | `pytest tests/ --cov=. --cov=app --cov-fail-under=80` |

### Pre-commit Hooks

```bash
pip install pre-commit
pre-commit install
```

Hooks run automatically on `git commit`: trailing-whitespace fix, ruff lint/format, mypy.

### CI / CD

GitHub Actions (`.github/workflows/ci.yml`) runs on every push / PR:

1. **lint** — ruff check, ruff format --check, mypy
2. **test** — pytest on Python 3.11 & 3.12, coverage ≥ 80 %
3. **security** — bandit static analysis, safety dependency check
4. **docker** — build image + healthcheck smoke test

## Testing

### Running All Tests

```bash
pytest tests/ --ignore=tests/test_property_based.py -v
```

### With Coverage Report

```bash
pytest tests/ --ignore=tests/test_property_based.py \
  --cov=. --cov=app --cov-config=pyproject.toml \
  --cov-report=term --cov-report=html
```

Coverage report is generated in `htmlcov/`.

## 🔒 Security

### Recommendations

1. **Use TLS** for production environments
2. **Store passwords securely** - use secrets, don't commit to repository
3. **Restrict API access** - use firewall and rate limiting
4. **Regularly update certificates**
5. **Use strong passwords** for MQTT broker

### Best Practices

- Use different API keys for different purposes
- Regularly rotate API keys
- Monitor logs for suspicious activity
- Use minimum necessary roles for API keys

## Monitoring

### Prometheus Metrics

If API is enabled, metrics are available at:

```
http://localhost:7900/metrics
```

### Logging

Logs are output to stdout/stderr. For production, centralized logging is recommended.

**Log format:**
```
[2025-01-27 12:00:00] [INFO] Connected to MQTT broker mqtt://your-broker-ip:1883
[2025-01-27 12:00:01] [INFO] Successfully subscribed to aroc/robot/fahrdummy-02/cmd/+
```

## Support

For questions and issues, create issues in the repository or contact the development team.

## License

Proprietary - AROC Technologies GmbH
