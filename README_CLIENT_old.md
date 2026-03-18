# MQTT Client Guide

Complete guide for MQTT clients connecting to the robot command service broker.

## Overview

This guide explains how to connect to the MQTT broker to:
- Send commands to robots
- Receive responses and status updates
- Subscribe to telemetry data
- Monitor robot state in real-time

## Broker Information

**Default Configuration:**
- **Broker Address:** `<BROKER_IP>` (or as configured)
- **Port 1883:** MQTT (unencrypted) - **Use only for testing**
- **Port 8883:** MQTTS (TLS encrypted) - **Recommended for production**
- **Username:** `<MQTT_USER>` (or as configured)
- **Password:** (provided by administrator)

## Quick Start

### 1. Get CA Certificate

First, obtain the CA certificate from the broker administrator or download it:

```bash
# Download CA certificate via API (if available)
curl https://mqtt.techvisioncloud.pl/api/v1/config/certificates/ca > ca.crt

# Or get it from administrator
```

### 2. Connect to Broker

**With TLS (Recommended):**
```bash
mosquitto_sub -h <BROKER_IP> -p 8883 \
  --cafile ca.crt \
  -u <MQTT_USER> -P '<MQTT_PASSWORD>' \
  -t "aroc/robot/fahrdummy-01/telemetry" \
  -v
```

**Without TLS (Testing only):**
```bash
mosquitto_sub -h <BROKER_IP> -p 1883 \
  -u <MQTT_USER> -P '<MQTT_PASSWORD>' \
  -t "aroc/robot/fahrdummy-01/telemetry" \
  -v
```

## Topic Structure

All topics follow the pattern: `aroc/robot/{ROBOT_ID}/{category}/{subcategory}`

### Command Topics (Client → Robot)

Send commands to these topics:

- `aroc/robot/{ROBOT_ID}/cmd/{SERVICE}` - HTTP proxy commands
  - `{SERVICE}` can be: `igus`, `xarm`, `symovo`, `robot`, `mqtt`
- `aroc/robot/{ROBOT_ID}/commands/navigateTo` - Navigation command (robot orchestration, manages complex multi-device waypoint navigation)
- `aroc/robot/{ROBOT_ID}/commands/position` - Drive position command (symovo, direct coordinate-based movement)
- `aroc/robot/{ROBOT_ID}/commands/cancel` - Cancel navigation
- `aroc/robot/{ROBOT_ID}/commands/estop` - Emergency stop

### Response Topics (Robot → Client)

Subscribe to these topics to receive responses:

- `aroc/robot/{ROBOT_ID}/resp/{SERVICE}` - HTTP command responses
- `aroc/robot/{ROBOT_ID}/status/system` - System status
- `aroc/robot/{ROBOT_ID}/status/connection` - Connection status
- `aroc/robot/{ROBOT_ID}/status/navigation` - Navigation status
- `aroc/robot/{ROBOT_ID}/telemetry` - Telemetry data

## Sending Commands

### HTTP Proxy Command

Send commands to robot services via MQTT:

**Topic:** `aroc/robot/{ROBOT_ID}/cmd/{SERVICE}`

**Example: Get Igus status**
```bash
mosquitto_pub -h <BROKER_IP> -p 8883 \
  --cafile ca.crt \
  -u <MQTT_USER> -P '<MQTT_PASSWORD>' \
  -t "aroc/robot/fahrdummy-01/cmd/igus" \
  -m '{
    "request_id": "cmd-001",
    "method": "GET",
    "path": "/status"
  }'
```

**Example: Move Igus lift**
```bash
mosquitto_pub -h <BROKER_IP> -p 8883 \
  --cafile ca.crt \
  -u <MQTT_USER> -P '<MQTT_PASSWORD>' \
  -t "aroc/robot/fahrdummy-01/cmd/igus" \
  -m '{
    "request_id": "move-001",
    "method": "POST",
    "path": "/move",
    "body": {
      "position": 30000,
      "velocity_percent": 25,
      "acceleration_percent": 25
    },
    "timeout": 30.0
  }'
```

**Command Fields:**
- `request_id` (required): Unique identifier for the request
- `method` (required): HTTP method (`GET`, `POST`, `PUT`, `DELETE`)
- `path` (required): API endpoint path
- `body` (optional): Request body (JSON object)
- `headers` (optional): Custom HTTP headers
- `timeout` (optional): Request timeout in seconds

### Navigation Commands

**Navigate to position:**
```bash
mosquitto_pub -h <BROKER_IP> -p 8883 \
  --cafile ca.crt \
  -u <MQTT_USER> -P '<MQTT_PASSWORD>' \
  -t "aroc/robot/fahrdummy-01/commands/navigateTo" \
  -m '{
    "command_id": "nav-001",
    "target_id": "position_A",
    "priority": "normal",
    "timestamp": "2025-01-27T12:00:00Z"
  }'
```

**Drive to position (symovo):**
```bash
mosquitto_pub -h <BROKER_IP> -p 8883 \
  --cafile ca.crt \
  -u <MQTT_USER> -P '<MQTT_PASSWORD>' \
  -t "aroc/robot/fahrdummy-01/commands/position" \
  -m '{
    "command_id": "pos-001",
    "x": 5.2,
    "y": 3.1,
    "theta": 0.785,
    "timestamp": "2025-01-27T12:00:00Z"
  }'
```

**Cancel navigation:**
```bash
mosquitto_pub -h <BROKER_IP> -p 8883 \
  --cafile ca.crt \
  -u <MQTT_USER> -P '<MQTT_PASSWORD>' \
  -t "aroc/robot/fahrdummy-01/commands/cancel" \
  -m '{
    "command_id": "cancel-001",
    "task_id": "task-123",
    "timestamp": "2025-01-27T12:00:00Z",
    "reason": "User cancelled"
  }'
```

**Emergency stop:**
```bash
mosquitto_pub -h <BROKER_IP> -p 8883 \
  --cafile ca.crt \
  -u <MQTT_USER> -P '<MQTT_PASSWORD>' \
  -t "aroc/robot/fahrdummy-01/commands/estop" \
  -m '{
    "command_id": "estop-001",
    "timestamp": "2025-01-27T12:00:00Z",
    "reason": "Emergency stop button pressed"
  }'
```

## Receiving Responses

### Subscribe to Response Topics

**Subscribe to all responses:**
```bash
mosquitto_sub -h <BROKER_IP> -p 8883 \
  --cafile ca.crt \
  -u <MQTT_USER> -P '<MQTT_PASSWORD>' \
  -t "aroc/robot/fahrdummy-01/resp/+" \
  -v
```

**Subscribe to specific service responses:**
```bash
mosquitto_sub -h <BROKER_IP> -p 8883 \
  --cafile ca.crt \
  -u <MQTT_USER> -P '<MQTT_PASSWORD>' \
  -t "aroc/robot/fahrdummy-01/resp/igus" \
  -v
```

### Response Format

**ACK Response (immediate):**
```json
{
  "type": "ack",
  "request_id": "cmd-001",
  "service": "igus",
  "success": true,
  "status_code": 200,
  "body": {
    "position": 30000
  },
  "error": null
}
```

**Result Response (after task completion):**
```json
{
  "type": "result",
  "request_id": "cmd-001",
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
  "request_id": "cmd-001",
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

## Subscribing to Status and Telemetry

### System Status

**Topic:** `aroc/robot/{ROBOT_ID}/status/system`

```bash
mosquitto_sub -h <BROKER_IP> -p 8883 \
  --cafile ca.crt \
  -u <MQTT_USER> -P '<MQTT_PASSWORD>' \
  -t "aroc/robot/fahrdummy-01/status/system" \
  -v
```

**Status Format:**
```json
{
  "type": "status",
  "status_type": "system",
  "robot_id": "fahrdummy-01",
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

### Connection Status

**Topic:** `aroc/robot/{ROBOT_ID}/status/connection`

```bash
mosquitto_sub -h <BROKER_IP> -p 8883 \
  --cafile ca.crt \
  -u <MQTT_USER> -P '<MQTT_PASSWORD>' \
  -t "aroc/robot/fahrdummy-01/status/connection" \
  -v
```

### Navigation Status

**Topic:** `aroc/robot/{ROBOT_ID}/status/navigation`

```bash
mosquitto_sub -h <BROKER_IP> -p 8883 \
  --cafile ca.crt \
  -u <MQTT_USER> -P '<MQTT_PASSWORD>' \
  -t "aroc/robot/fahrdummy-01/status/navigation" \
  -v
```

**Status Values:**
- `acknowledged` - Command received
- `duplicate` - Command already processed
- `completed` - Task completed successfully
- `failed` - Task failed
- `timeout` - Task timed out
- `rejected` - Command rejected

### Telemetry

**Topic:** `aroc/robot/{ROBOT_ID}/telemetry`

```bash
mosquitto_sub -h <BROKER_IP> -p 8883 \
  --cafile ca.crt \
  -u <MQTT_USER> -P '<MQTT_PASSWORD>' \
  -t "aroc/robot/fahrdummy-01/telemetry" \
  -v
```

**Telemetry Format:**
```json
{
  "robot_id": "fahrdummy-01",
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
    "state": "navigating"
  },
  "components": {
    "igus": {
      "connected": true,
      "homed": true,
      "is_moving": false,
      "position_cm": 150.0
    }
  }
}
```

## Python Client Examples

### Basic Client (without TLS)

```python
import paho.mqtt.client as mqtt
import json
import uuid
import time

def on_connect(client, userdata, flags, rc):
    print(f"Connected with result code {rc}")
    # Subscribe to responses
    client.subscribe("aroc/robot/fahrdummy-01/resp/+")
    client.subscribe("aroc/robot/fahrdummy-01/status/+")
    client.subscribe("aroc/robot/fahrdummy-01/telemetry")

def on_message(client, userdata, msg):
    payload = json.loads(msg.payload.decode())
    print(f"Topic: {msg.topic}")
    print(f"Payload: {json.dumps(payload, indent=2)}")

# Create client
client = mqtt.Client()
client.username_pw_set("<MQTT_USER>", "<MQTT_PASSWORD>")
client.on_connect = on_connect
client.on_message = on_message

# Connect (port 1883, without TLS)
client.connect("<BROKER_IP>", 1883, 60)
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

### Client with TLS (Recommended)

```python
import paho.mqtt.client as mqtt
import json
import uuid
import time

def on_connect(client, userdata, flags, rc):
    print(f"Connected with result code {rc}")
    client.subscribe("aroc/robot/fahrdummy-01/resp/+")
    client.subscribe("aroc/robot/fahrdummy-01/status/+")
    client.subscribe("aroc/robot/fahrdummy-01/telemetry")

def on_message(client, userdata, msg):
    payload = json.loads(msg.payload.decode())
    print(f"Topic: {msg.topic}")
    print(f"Payload: {json.dumps(payload, indent=2)}")

# Create client
client = mqtt.Client()
client.username_pw_set("<MQTT_USER>", "<MQTT_PASSWORD>")

# Configure TLS
client.tls_set(
    ca_certs="ca.crt",           # CA certificate
    certfile="client.crt",       # Client certificate (optional)
    keyfile="client.key"         # Private key (optional)
)
# client.tls_insecure_set(True)  # Testing only!

client.on_connect = on_connect
client.on_message = on_message

# Connect (port 8883, with TLS)
client.connect("<BROKER_IP>", 8883, 60)
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

### Advanced Client with Reconnection

```python
import paho.mqtt.client as mqtt
import json
import uuid
import time
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class RobotMQTTClient:
    def __init__(self, broker, port, username, password, robot_id, ca_certs=None):
        self.broker = broker
        self.port = port
        self.username = username
        self.password = password
        self.robot_id = robot_id
        self.ca_certs = ca_certs
        self.client = None
        self.connected = False
        
    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected = True
            logger.info("Connected to MQTT broker")
            # Subscribe to all relevant topics
            client.subscribe(f"aroc/robot/{self.robot_id}/resp/+")
            client.subscribe(f"aroc/robot/{self.robot_id}/status/+")
            client.subscribe(f"aroc/robot/{self.robot_id}/telemetry")
        else:
            logger.error(f"Failed to connect: {rc}")
            self.connected = False
    
    def on_disconnect(self, client, userdata, rc):
        self.connected = False
        logger.warning(f"Disconnected from broker: {rc}")
    
    def on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
            logger.info(f"Received on {msg.topic}: {payload}")
            # Handle message based on topic
            if msg.topic.startswith(f"aroc/robot/{self.robot_id}/resp/"):
                self.handle_response(payload)
            elif msg.topic.startswith(f"aroc/robot/{self.robot_id}/status/"):
                self.handle_status(payload)
            elif msg.topic == f"aroc/robot/{self.robot_id}/telemetry":
                self.handle_telemetry(payload)
        except Exception as e:
            logger.error(f"Error processing message: {e}")
    
    def handle_response(self, payload):
        """Handle command response."""
        if payload.get("success"):
            logger.info(f"Command {payload.get('request_id')} succeeded")
        else:
            logger.error(f"Command {payload.get('request_id')} failed: {payload.get('error')}")
    
    def handle_status(self, payload):
        """Handle status update."""
        logger.info(f"Status update: {payload.get('status_type')}")
    
    def handle_telemetry(self, payload):
        """Handle telemetry data."""
        logger.info(f"Telemetry: battery={payload.get('data', {}).get('battery_percent')}%")
    
    def connect(self):
        """Connect to MQTT broker."""
        self.client = mqtt.Client()
        self.client.username_pw_set(self.username, self.password)
        
        if self.ca_certs:
            self.client.tls_set(ca_certs=self.ca_certs)
        
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message
        
        self.client.connect(self.broker, self.port, 60)
        self.client.loop_start()
        
        # Wait for connection
        timeout = 10
        while not self.connected and timeout > 0:
            time.sleep(0.1)
            timeout -= 0.1
        
        return self.connected
    
    def send_command(self, service, method, path, body=None, headers=None):
        """Send HTTP proxy command."""
        if not self.connected:
            logger.error("Not connected to broker")
            return None
        
        request_id = str(uuid.uuid4())
        command = {
            "request_id": request_id,
            "method": method,
            "path": path
        }
        
        if body:
            command["body"] = body
        if headers:
            command["headers"] = headers
        
        topic = f"aroc/robot/{self.robot_id}/cmd/{service}"
        result = self.client.publish(topic, json.dumps(command), qos=1)
        
        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            logger.info(f"Command sent: {request_id}")
            return request_id
        else:
            logger.error(f"Failed to send command: {result.rc}")
            return None
    
    def disconnect(self):
        """Disconnect from broker."""
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()

# Usage
if __name__ == "__main__":
    client = RobotMQTTClient(
        broker="<BROKER_IP>",
        port=8883,
        username="<MQTT_USER>",
        password="<MQTT_PASSWORD>",
        robot_id="fahrdummy-01",
        ca_certs="ca.crt"
    )
    
    if client.connect():
        # Send command
        client.send_command("igus", "GET", "/status")
        
        # Keep running
        try:
            time.sleep(60)
        except KeyboardInterrupt:
            pass
        finally:
            client.disconnect()
```

## Using Certificates

### Getting Certificates

**Download CA certificate:**
```bash
curl https://mqtt.techvisioncloud.pl/api/v1/config/certificates/ca > ca.crt
```

**Or get from administrator:**
- CA certificate: `ca.crt`
- Client certificate: `client.crt` (optional, for mutual TLS)
- Private key: `client.key` (optional, for mutual TLS)

### Using with mosquitto_pub/sub

**Basic (CA only):**
```bash
mosquitto_pub -h <BROKER_IP> -p 8883 \
  --cafile ca.crt \
  -u <MQTT_USER> -P 'password' \
  -t "topic" -m "<MQTT_PASSWORD>"
```

**With client certificate (mutual TLS):**
```bash
mosquitto_pub -h <BROKER_IP> -p 8883 \
  --cafile ca.crt \
  --cert client.crt \
  --key client.key \
  -u <MQTT_USER> -P '<MQTT_PASSWORD>' \
  -t "topic" -m "message"
```

**With insecure TLS (testing only):**
```bash
mosquitto_pub -h <BROKER_IP> -p 8883 \
  --cafile ca.crt \
  --insecure \
  -u <MQTT_USER> -P '<MQTT_PASSWORD>' \
  -t "topic" -m "message"
```

## Best Practices

1. **Always use TLS in production** - Port 8883 with CA certificate
2. **Use unique request_id** - UUID v4 recommended
3. **Subscribe before publishing** - Ensure you're subscribed to response topics
4. **Handle reconnection** - Implement automatic reconnection logic
5. **Use QoS 1** - For guaranteed message delivery
6. **Monitor connection status** - Subscribe to `status/connection` topic
7. **Handle errors gracefully** - Check `success` field in responses
8. **Respect timeouts** - Don't wait indefinitely for responses

## Troubleshooting

### Connection Issues

**Problem: Cannot connect to broker**
```bash
# Test connection
mosquitto_pub -h <BROKER_IP> -p 8883 \
  --cafile ca.crt \
  -u <MQTT_USER> -P '<MQTT_PASSWORD>' \
  -t "test" -m "test" -q 1
```

**Check:**
- Broker address and port are correct
- CA certificate path is correct
- Username and password are correct
- Firewall allows connection
- Broker is running

### Certificate Issues

**Problem: TLS handshake failed**
- Verify CA certificate is valid and not expired
- Check certificate file permissions
- Ensure certificate is in PEM format

**Problem: Certificate verification failed**
- Use `--insecure` flag for testing (NOT for production)
- Verify CA certificate matches broker certificate

### Message Delivery Issues

**Problem: Messages not received**
- Verify subscription to correct topics
- Check QoS level (use QoS 1 for guaranteed delivery)
- Ensure client is connected (`on_connect` callback received)
- Check topic names match exactly (case-sensitive)

**Problem: Responses not received**
- Subscribe to `resp/+` to catch all responses
- Verify `request_id` matches sent command
- Check robot service is running and responding

## Security Considerations

1. **Never use port 1883 in production** - It's unencrypted
2. **Protect certificates** - Store securely, don't commit to version control
3. **Use strong passwords** - For MQTT broker authentication
4. **Rotate credentials** - Regularly update passwords and certificates
5. **Monitor connections** - Watch for unauthorized access
6. **Use mutual TLS** - For additional security (client certificates)

## Additional Resources

- **Main Service README:** See `README.md` for service configuration
- **MQTT Protocol:** See `docs/mqtt_contract.md` for full protocol specification
- **API Documentation:** https://mqtt.techvisioncloud.pl/docs (if API is enabled)

