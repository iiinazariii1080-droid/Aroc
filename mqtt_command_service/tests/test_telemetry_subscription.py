#!/usr/bin/env python3
"""
Integration test for MQTT broker connection and telemetry subscription.

This test connects to the remote MQTT broker and subscribes to telemetry topics
to verify that the connection works and telemetry data is being published.

Usage:
    # Run with default config (from environment/DB)
    python tests/test_telemetry_subscription.py

    # Run with custom config
    python tests/test_telemetry_subscription.py --broker 82.165.177.194 --port 8883 --robot-id fahrdummy-01
"""

import argparse
import json
import logging
import os
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from paho.mqtt import client as mqtt_client

from constants import MQTT_TLS_PORT

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


class TelemetrySubscriber:
    """MQTT subscriber for telemetry testing."""

    def __init__(
        self,
        broker: str,
        port: int,
        username: str,
        password: str,
        robot_id: str,
        use_tls: bool = True,
        ca_certs: str | None = None,
        tls_insecure: bool = False,
        timeout: float = 30.0,
    ):
        self.broker = broker
        self.port = port
        self.username = username
        self.password = password
        self.robot_id = robot_id
        self.use_tls = use_tls
        self.ca_certs = ca_certs
        self.tls_insecure = tls_insecure
        self.timeout = timeout

        self.client: mqtt_client.Client | None = None
        self.connected = False
        self.messages_received: list[dict[str, Any]] = []
        self.connection_event = threading.Event()
        self.message_event = threading.Event()

    def on_connect(self, client, userdata, flags, rc):
        """Callback for MQTT connection."""
        if rc == 0:
            logger.info("✅ Connected to MQTT broker %s://%s:%s",
                       "mqtts" if self.use_tls else "mqtt",
                       self.broker, self.port)
            self.connected = True
            self.connection_event.set()
        else:
            error_msgs = {
                1: "Connection refused - incorrect protocol version",
                2: "Connection refused - invalid client identifier",
                3: "Connection refused - server unavailable",
                4: "Connection refused - bad username or password",
                5: "Connection refused - not authorised",
            }
            error_msg = error_msgs.get(rc, f"Connection failed with code {rc}")
            logger.error("❌ Failed to connect: %s", error_msg)
            self.connection_event.set()

    def on_disconnect(self, client, userdata, rc):
        """Callback for MQTT disconnection."""
        logger.info("Disconnected from MQTT broker (rc=%s)", rc)
        self.connected = False

    def on_message(self, client, userdata, msg):
        """Callback for received MQTT messages."""
        try:
            payload = json.loads(msg.payload.decode('utf-8'))
            message_info = {
                "topic": msg.topic,
                "payload": payload,
                "qos": msg.qos,
                "timestamp": datetime.now(UTC).isoformat(),
            }
            self.messages_received.append(message_info)
            self.message_event.set()

            logger.info("📨 Received message on topic: %s", msg.topic)
            logger.debug("Payload: %s", json.dumps(payload, indent=2))

        except json.JSONDecodeError as e:
            logger.error("Failed to parse JSON payload: %s", e)
            logger.debug("Raw payload: %s", msg.payload)
        except Exception as e:
            logger.error("Error processing message: %s", e, exc_info=True)

    def on_subscribe(self, client, userdata, mid, granted_qos):
        """Callback for subscription confirmation."""
        logger.info("✅ Subscribed to topic (mid=%s, qos=%s)", mid, granted_qos)

    def connect(self) -> bool:
        """Connect to MQTT broker."""
        client_id = f"telemetry_test_{int(time.time())}"
        self.client = mqtt_client.Client(client_id=client_id, clean_session=True)

        # Set credentials
        if self.username:
            self.client.username_pw_set(self.username, self.password)

        # Configure TLS if needed
        if self.use_tls:
            try:
                self.client.tls_set(
                    ca_certs=self.ca_certs,
                    certfile=None,
                    keyfile=None,
                )
                self.client.tls_insecure_set(self.tls_insecure)
                logger.info("TLS configured (CA: %s, insecure: %s)",
                           self.ca_certs or "system default",
                           self.tls_insecure)
            except Exception as e:
                logger.error("Failed to configure TLS: %s", e)
                return False

        # Set callbacks
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message
        self.client.on_subscribe = self.on_subscribe

        # Connect
        try:
            logger.info("Connecting to %s://%s:%s...",
                       "mqtts" if self.use_tls else "mqtt",
                       self.broker, self.port)
            self.client.connect(self.broker, self.port, keepalive=60)
            self.client.loop_start()

            # Wait for connection
            if self.connection_event.wait(timeout=self.timeout):
                return self.connected
            else:
                logger.error("Connection timeout after %s seconds", self.timeout)
                return False

        except Exception as e:
            logger.error("Failed to connect: %s", e, exc_info=True)
            return False

    def subscribe_to_telemetry(self) -> bool:
        """Subscribe to telemetry topic."""
        if not self.connected or not self.client:
            logger.error("Not connected to broker")
            return False

        topic = f"aroc/robot/{self.robot_id}/telemetry"
        logger.info("Subscribing to topic: %s", topic)

        result, mid = self.client.subscribe(topic, qos=1)
        if result == 0:
            logger.info("Subscription request sent (mid=%s)", mid)
            return True
        else:
            logger.error("Failed to subscribe (result=%s)", result)
            return False

    def subscribe_to_status_topics(self) -> bool:
        """Subscribe to all status topics for the robot."""
        if not self.connected or not self.client:
            logger.error("Not connected to broker")
            return False

        topics = [
            f"aroc/robot/{self.robot_id}/status/system",
            f"aroc/robot/{self.robot_id}/status/connection",
            f"aroc/robot/{self.robot_id}/status/navigation",
        ]

        all_success = True
        for topic in topics:
            logger.info("Subscribing to topic: %s", topic)
            result, mid = self.client.subscribe(topic, qos=1)
            if result == 0:
                logger.info("Subscription request sent for %s (mid=%s)", topic, mid)
            else:
                logger.error("Failed to subscribe to %s (result=%s)", topic, result)
                all_success = False

        return all_success

    def wait_for_messages(self, count: int = 1, timeout: float = 60.0) -> bool:
        """Wait for at least N messages."""
        logger.info("Waiting for %d message(s) (timeout: %s seconds)...", count, timeout)

        start_time = time.time()
        while len(self.messages_received) < count:
            if time.time() - start_time > timeout:
                logger.warning("Timeout waiting for messages. Received: %d/%d",
                             len(self.messages_received), count)
                return False

            # Wait for new message
            if self.message_event.wait(timeout=1.0):
                self.message_event.clear()

            if not self.connected:
                logger.error("Connection lost while waiting for messages")
                return False

        logger.info("✅ Received %d message(s)", len(self.messages_received))
        return True

    def validate_telemetry_message(self, message: dict[str, Any]) -> bool:
        """Validate telemetry message format."""
        topic = message.get("topic", "")
        payload = message.get("payload", {})

        # Check topic format
        expected_topic = f"aroc/robot/{self.robot_id}/telemetry"
        if topic != expected_topic:
            logger.error("❌ Invalid topic: %s (expected: %s)", topic, expected_topic)
            return False

        # Check required fields
        required_fields = ["robot_id", "timestamp", "data"]
        for field in required_fields:
            if field not in payload:
                logger.error("❌ Missing required field: %s", field)
                return False

        # Check robot_id
        if payload.get("robot_id") != self.robot_id:
            logger.error("❌ Robot ID mismatch: %s (expected: %s)",
                        payload.get("robot_id"), self.robot_id)
            return False

        # Check data structure
        data = payload.get("data", {})
        required_data_fields = ["pose", "velocity", "battery_percent", "state"]
        for field in required_data_fields:
            if field not in data:
                logger.warning("⚠️  Missing data field: %s", field)

        # Check pose structure
        pose = data.get("pose", {})
        if not isinstance(pose, dict):
            logger.warning("⚠️  Pose is not a dict")
        else:
            pose_fields = ["x", "y", "theta", "map_id"]
            for field in pose_fields:
                if field not in pose:
                    logger.warning("⚠️  Missing pose field: %s", field)

        # Check velocity structure
        velocity = data.get("velocity", {})
        if not isinstance(velocity, dict):
            logger.warning("⚠️  Velocity is not a dict")
        else:
            velocity_fields = ["vx", "vy", "omega"]
            for field in velocity_fields:
                if field not in velocity:
                    logger.warning("⚠️  Missing velocity field: %s", field)

        logger.info("✅ Telemetry message format is valid")
        return True

    def disconnect(self):
        """Disconnect from MQTT broker."""
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
            logger.info("Disconnected from broker")

    def print_summary(self):
        """Print summary of received messages."""
        print("\n" + "="*80)
        print("📊 TEST SUMMARY")
        print("="*80)
        print(f"Broker: {self.broker}:{self.port} ({'TLS' if self.use_tls else 'no TLS'})")
        print(f"Robot ID: {self.robot_id}")
        print(f"Messages received: {len(self.messages_received)}")
        print()

        if self.messages_received:
            print("📨 Received Messages:")
            for i, msg in enumerate(self.messages_received, 1):
                print(f"\n  Message {i}:")
                print(f"    Topic: {msg['topic']}")
                print(f"    QoS: {msg['qos']}")
                print(f"    Timestamp: {msg['timestamp']}")

                payload = msg['payload']
                if 'robot_id' in payload:
                    print(f"    Robot ID: {payload['robot_id']}")
                if 'timestamp' in payload:
                    print(f"    Payload timestamp: {payload['timestamp']}")
                if 'data' in payload:
                    data = payload['data']
                    if 'pose' in data:
                        pose = data['pose']
                        print(f"    Pose: x={pose.get('x')}, y={pose.get('y')}, theta={pose.get('theta')}")
                    if 'battery_percent' in data:
                        print(f"    Battery: {data['battery_percent']}%")
                    if 'state' in data:
                        print(f"    State: {data['state']}")
        else:
            print("⚠️  No messages received")

        print("\n" + "="*80)


def load_config_from_env() -> dict[str, Any]:
    """Load MQTT configuration from environment variables or DB."""
    try:
        from config import load_bridge_config
        config = load_bridge_config()
        return {
            "broker": config.broker,
            "port": config.broker_port,
            "username": config.mqtt_user,
            "password": config.mqtt_password,
            "robot_id": config.robot_id,
            "use_tls": config.mqtt_use_tls,
            "ca_certs": config.mqtt_ca_certs,
            "tls_insecure": config.mqtt_tls_insecure,
        }
    except Exception as e:
        logger.warning("Failed to load config from DB: %s", e)
        # Fallback to environment variables
        from constants import CERT_CA_FILE
        return {
            "broker": os.getenv("MQTT_BROKER", "localhost"),
            "port": int(os.getenv("MQTT_PORT", str(MQTT_TLS_PORT))),
            "username": os.getenv("MQTT_USER", ""),
            "password": os.getenv("MQTT_PASS", ""),
            "robot_id": os.getenv("ROBOT_ID", "fahrdummy-01"),
            "use_tls": os.getenv("MQTT_USE_TLS", "true").lower() in ("true", "1", "yes"),
            "ca_certs": str(CERT_CA_FILE) if CERT_CA_FILE.exists() else None,
            "tls_insecure": os.getenv("MQTT_TLS_INSECURE", "true").lower() in ("true", "1", "yes"),
        }


def main():
    """Main test function."""
    parser = argparse.ArgumentParser(description="Test MQTT broker connection and telemetry subscription")
    parser.add_argument("--broker", help="MQTT broker address")
    parser.add_argument("--port", type=int, help="MQTT broker port")
    parser.add_argument("--username", help="MQTT username")
    parser.add_argument("--password", help="MQTT password")
    parser.add_argument("--robot-id", help="Robot ID")
    parser.add_argument("--use-tls", action="store_true", help="Use TLS")
    parser.add_argument("--no-tls", action="store_true", help="Disable TLS")
    parser.add_argument("--ca-certs", help="Path to CA certificate file")
    parser.add_argument("--tls-insecure", action="store_true", help="Disable TLS certificate verification")
    parser.add_argument("--timeout", type=float, default=30.0, help="Connection timeout (seconds)")
    parser.add_argument("--wait-time", type=float, default=60.0, help="Time to wait for messages (seconds)")
    parser.add_argument("--message-count", type=int, default=3, help="Number of messages to wait for")
    parser.add_argument("--subscribe-status", action="store_true", help="Also subscribe to status topics")

    args = parser.parse_args()

    # Load config
    config = load_config_from_env()

    # Override with command line arguments
    if args.broker:
        config["broker"] = args.broker
    if args.port:
        config["port"] = args.port
    if args.username:
        config["username"] = args.username
    if args.password:
        config["password"] = args.password
    if args.robot_id:
        config["robot_id"] = args.robot_id
    if args.use_tls:
        config["use_tls"] = True
    if args.no_tls:
        config["use_tls"] = False
    if args.ca_certs:
        config["ca_certs"] = args.ca_certs
    if args.tls_insecure:
        config["tls_insecure"] = True

    # Print configuration (without password)
    print("="*80)
    print("🔧 TEST CONFIGURATION")
    print("="*80)
    print(f"Broker: {config['broker']}:{config['port']}")
    print(f"TLS: {config['use_tls']}")
    if config['use_tls']:
        print(f"CA Certs: {config['ca_certs'] or 'system default'}")
        print(f"TLS Insecure: {config['tls_insecure']}")
    print(f"Username: {config['username']}")
    print(f"Password: {'***' if config['password'] else '(empty)'}")
    print(f"Robot ID: {config['robot_id']}")
    print("="*80)
    print()

    # Create subscriber
    subscriber = TelemetrySubscriber(
        broker=config["broker"],
        port=config["port"],
        username=config["username"],
        password=config["password"],
        robot_id=config["robot_id"],
        use_tls=config["use_tls"],
        ca_certs=config["ca_certs"],
        tls_insecure=config["tls_insecure"],
        timeout=args.timeout,
    )

    try:
        # Connect
        if not subscriber.connect():
            logger.error("❌ Failed to connect to broker")
            return 1

        # Subscribe to telemetry
        if not subscriber.subscribe_to_telemetry():
            logger.error("❌ Failed to subscribe to telemetry topic")
            return 1

        # Optionally subscribe to status topics
        if args.subscribe_status:
            subscriber.subscribe_to_status_topics()

        # Wait for messages
        logger.info("Waiting for telemetry messages...")
        if not subscriber.wait_for_messages(count=args.message_count, timeout=args.wait_time):
            logger.warning("⚠️  Did not receive expected number of messages")

        # Validate messages
        telemetry_messages = [
            msg for msg in subscriber.messages_received
            if msg["topic"].endswith("/telemetry")
        ]

        if telemetry_messages:
            logger.info("Validating telemetry messages...")
            all_valid = True
            for msg in telemetry_messages:
                if not subscriber.validate_telemetry_message(msg):
                    all_valid = False

            if all_valid:
                logger.info("✅ All telemetry messages are valid")
            else:
                logger.warning("⚠️  Some telemetry messages have validation issues")
        else:
            logger.warning("⚠️  No telemetry messages received")

        # Print summary
        subscriber.print_summary()

        # Return success if we got at least one message
        return 0 if len(subscriber.messages_received) > 0 else 1

    except KeyboardInterrupt:
        logger.info("Test interrupted by user")
        return 130
    except Exception as e:
        logger.error("Test failed with error: %s", e, exc_info=True)
        return 1
    finally:
        subscriber.disconnect()


if __name__ == "__main__":
    sys.exit(main())

