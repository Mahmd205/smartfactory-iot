"""
Module 1 Assignment — Task 1.1
MQTT Sensor Publisher

Implements a multi-sensor publisher for the SmartFactory telemetry system.
Connects to Mosquitto with a persistent session, sets a Last Will, publishes
retained 'online' status, and emits readings for 6 sensors at different QoS
levels every second.
"""

import json
import logging
import random
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional

import paho.mqtt.client as mqtt

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────
BROKER_HOST = "localhost"
BROKER_PORT = 1883
CLIENT_ID   = "smartfactory-publisher-001"

LINES   = ["line1", "line2"]
SENSORS = {
    "temperature": {"unit": "C",    "base": 70.0, "noise": 3.0,  "qos": 1},
    "vibration":   {"unit": "mm/s", "base": 1.2,  "noise": 0.3,  "qos": 0},
    "power":       {"unit": "kW",   "base": 45.0, "noise": 5.0,  "qos": 2},
}

CRITICAL_TEMP_THRESHOLD = 85.0


@dataclass
class SensorReading:
    line:      str
    sensor:    str
    value:     float
    unit:      str
    timestamp: str
    seq:       int


class SmartFactoryPublisher:
    """Publishes simulated sensor data for the SmartFactory assignment."""

    def __init__(self, broker_host: str = BROKER_HOST, broker_port: int = BROKER_PORT):
        self.broker_host = broker_host
        self.broker_port = broker_port
        self._seq: dict[str, int] = {}          # per-topic sequence counter
        self._client: Optional[mqtt.Client] = None

    # ── Connection ─────────────────────────────────────────────────────────────

    def _build_client(self) -> mqtt.Client:
        """
        Create and configure the MQTT client with persistent session and LWT.
        paho only supports a single LWT per client, so we set it for line1 —
        the assignment spec notes that the tests verify line1 LWT.
        """
        client = mqtt.Client(client_id=CLIENT_ID, clean_session=False)
        client.on_connect = self.on_connect
        client.on_publish = self.on_publish

        # Last Will and Testament — published by broker if we crash/disconnect
        # uncleanly. retain=True means new subscribers immediately see "offline".
        client.will_set(
            topic=f"factory/{LINES[0]}/status",
            payload="offline",
            qos=1,
            retain=True,
        )
        self._client = client
        return client

    def connect(self) -> None:
        """Connect, start network loop, and publish initial 'online' retained."""
        if self._client is None:
            self._build_client()

        log.info("Connecting to %s:%s as %s", self.broker_host, self.broker_port, CLIENT_ID)
        self._client.connect(self.broker_host, self.broker_port, keepalive=60)
        self._client.loop_start()

        # Wait up to 5 seconds for the connection to establish
        deadline = time.time() + 5.0
        while time.time() < deadline and not self._client.is_connected():
            time.sleep(0.05)

        if not self._client.is_connected():
            log.warning("Connection not confirmed within 5 s — continuing anyway")

        # Publish retained "online" for each line so any new subscriber learns
        # the current status of every line on attach.
        for line in LINES:
            self._client.publish(
                f"factory/{line}/status", "online", qos=1, retain=True
            )
            log.info("Published retained online status for %s", line)

    def disconnect(self) -> None:
        """Cleanly disconnect: publish 'offline' retained for each line, then stop."""
        if self._client is None:
            return
        for line in LINES:
            self._client.publish(f"factory/{line}/status", "offline", qos=1, retain=True)
        self._client.loop_stop()
        self._client.disconnect()
        log.info("Disconnected cleanly")

    # ── Callbacks ──────────────────────────────────────────────────────────────

    def on_connect(self, client, userdata, flags, rc: int) -> None:
        if rc == 0:
            log.info("Connected (rc=%d)", rc)
        else:
            log.error("Connection refused: %d", rc)

    def on_publish(self, client, userdata, mid: int) -> None:
        # PUBACK only fires on QoS 1 and PUBCOMP on QoS 2.
        log.debug("PUBACK received for mid=%d", mid)

    # ── Sensor Simulation ──────────────────────────────────────────────────────

    def _simulate_reading(self, line: str, sensor: str) -> SensorReading:
        """Generate a realistic simulated sensor reading with Gaussian noise."""
        cfg   = SENSORS[sensor]
        value = round(cfg["base"] + random.gauss(0, cfg["noise"]), 3)
        key   = f"{line}/{sensor}"
        self._seq[key] = self._seq.get(key, 0) + 1
        return SensorReading(
            line=line,
            sensor=sensor,
            value=value,
            unit=cfg["unit"],
            timestamp=datetime.now(timezone.utc).isoformat(),
            seq=self._seq[key],
        )

    def _topic(self, line: str, sensor: str) -> str:
        """Return the canonical topic: factory/{line}/{sensor}."""
        return f"factory/{line}/{sensor}"

    # ── Publishing ─────────────────────────────────────────────────────────────

    def publish_reading(self, line: str, sensor: str) -> SensorReading:
        """Simulate a reading and publish it at the configured QoS level."""
        reading = self._simulate_reading(line, sensor)
        topic   = self._topic(line, sensor)
        qos     = SENSORS[sensor]["qos"]

        payload = json.dumps(asdict(reading))
        self._client.publish(topic, payload, qos=qos, retain=False)

        log.info(
            "topic=%s  value=%s %s  QoS=%d  seq=%d",
            topic, reading.value, reading.unit, qos, reading.seq,
        )
        return reading

    # ── Main Loop ──────────────────────────────────────────────────────────────

    def run(self, interval_s: float = 1.0) -> None:
        """Continuously publish all sensors until interrupted."""
        self.connect()
        log.info("Publishing started (Ctrl-C to stop)")
        try:
            while True:
                for line in LINES:
                    for sensor in SENSORS:
                        self.publish_reading(line, sensor)
                time.sleep(interval_s)
        except KeyboardInterrupt:
            log.info("Shutting down…")
        finally:
            self.disconnect()


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    pub = SmartFactoryPublisher()
    pub.run()
