"""
Module 1 Assignment — Task 1.2
MQTT Wildcard Subscriber

Subscribes to all SmartFactory topics with two overlapping subscriptions
(wildcard at QoS 1 plus an additional temperature-only subscription at
QoS 2), detects critical temperature events, and emits a per-topic count
summary every 30 seconds.
"""

import json
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import paho.mqtt.client as mqtt

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────
BROKER_HOST  = "localhost"
BROKER_PORT  = 1883
CLIENT_ID    = "smartfactory-subscriber-001"

TOPIC_ALL        = "factory/#"               # all factory messages
TOPIC_TEMP       = "factory/+/temperature"   # temperatures only — separate sub at QoS 2

CRITICAL_TEMP    = 85.0
SUMMARY_INTERVAL = 30   # seconds


class SmartFactorySubscriber:
    """Subscribes to SmartFactory sensor topics and processes incoming data."""

    def __init__(self, broker_host: str = BROKER_HOST, broker_port: int = BROKER_PORT):
        self.broker_host  = broker_host
        self.broker_port  = broker_port
        self._client      = mqtt.Client(client_id=CLIENT_ID, clean_session=False)
        self._msg_counts: dict[str, int] = defaultdict(int)
        self._last_summary = time.time()
        self._alerts_fired = 0

        self._client.on_connect = self.on_connect
        self._client.on_message = self.on_message

    # ── Connection ─────────────────────────────────────────────────────────────

    def on_connect(self, client, userdata, flags, rc: int) -> None:
        if rc == 0:
            log.info("Connected to broker")
            # Wildcard subscription: factory/# at QoS 1.
            client.subscribe(TOPIC_ALL, qos=1)
            # Additional QoS-2 subscription for temperature so we get
            # exactly-once delivery for critical-alert candidates.
            client.subscribe(TOPIC_TEMP, qos=2)
            log.info("Subscribed to %s (QoS 1) and %s (QoS 2)", TOPIC_ALL, TOPIC_TEMP)
        else:
            log.error("Connection failed (rc=%d)", rc)

    # ── Message Handling ───────────────────────────────────────────────────────

    def on_message(self, client, userdata, msg: mqtt.MQTTMessage) -> None:
        """Handle every incoming message: count, parse, display, alert."""
        self._msg_counts[msg.topic] += 1

        # Best-effort JSON decode — status messages are plain strings.
        try:
            payload: Any = json.loads(msg.payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            try:
                payload = msg.payload.decode()
            except Exception:
                payload = msg.payload

        self._print_message(msg, payload)

        if msg.topic.endswith("/temperature"):
            self._check_temperature_alert(msg.topic, payload)

        if time.time() - self._last_summary >= SUMMARY_INTERVAL:
            self._print_summary()
            self._last_summary = time.time()

    def _print_message(self, msg: mqtt.MQTTMessage, payload: Any) -> None:
        """Format one received message with parsed payload, QoS, and retain flag."""
        ts = datetime.now().strftime("%H:%M:%S")
        if isinstance(payload, dict):
            payload_str = json.dumps(payload, sort_keys=True)
        else:
            payload_str = str(payload)
        print(
            f"[{ts}] topic={msg.topic}  payload={payload_str}  "
            f"QoS={msg.qos}  retain={msg.retain}"
        )

    def _check_temperature_alert(self, topic: str, payload: Any) -> None:
        """Fire a CRITICAL alert banner if temperature > threshold."""
        if not isinstance(payload, dict):
            return
        value = payload.get("value")
        if not isinstance(value, (int, float)):
            return
        if value <= CRITICAL_TEMP:
            return

        self._alerts_fired += 1
        ts = payload.get("timestamp") or datetime.now(timezone.utc).isoformat()
        print("╔══════════════════════════════════════════════════════════╗")
        print(f"║  ⚠ CRITICAL ALERT — {topic}")
        print(f"║  Temperature: {value}°C  (threshold: {CRITICAL_TEMP}°C)")
        print(f"║  Time: {ts}")
        print("╚══════════════════════════════════════════════════════════╝")

    def _print_summary(self) -> None:
        """Periodic per-topic message counts."""
        print("── Message Summary ──────────────────────────────────────")
        total = 0
        for topic in sorted(self._msg_counts):
            count = self._msg_counts[topic]
            print(f"  {topic:<50}  {count:>6} msgs")
            total += count
        print(f"  Total: {total} messages  |  Alerts fired: {self._alerts_fired}")
        print("─────────────────────────────────────────────────────────")

    # ── Run ────────────────────────────────────────────────────────────────────

    def run(self) -> None:
        """Connect and block until interrupted."""
        self._client.connect(self.broker_host, self.broker_port, keepalive=60)
        log.info("Listening for messages (Ctrl-C to stop)")
        try:
            self._client.loop_forever()
        except KeyboardInterrupt:
            log.info("Subscriber stopped")
        finally:
            self._client.disconnect()


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    sub = SmartFactorySubscriber()
    sub.run()
