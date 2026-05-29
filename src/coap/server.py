"""
Module 1 Assignment — Task 2.1
CoAP Sensor Resource Server

Exposes sensor, actuator and firmware-manifest resources over CoAP.
- /factory/{line}/{sensor}   GET (observable for temp/vibration)
- /actuator/line1/fan        GET, PUT
- /factory/manifest          GET (Block2 transfer; payload >= 3 KB)

Run with:  python -m src.coap.server
"""

import asyncio
import hashlib
import json
import logging
import random
from datetime import datetime, timezone

import aiocoap
import aiocoap.resource as resource
from aiocoap import Code, Message
from aiocoap.numbers.contentformat import ContentFormat

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")
log = logging.getLogger(__name__)

JSON_CF = ContentFormat.JSON  # value 50

# ── Sensor simulation helpers ─────────────────────────────────────────────────

SENSOR_CONFIG = {
    "temperature": {"unit": "C",    "base": 70.0, "noise": 3.0},
    "vibration":   {"unit": "mm/s", "base": 1.2,  "noise": 0.3},
    "power":       {"unit": "kW",   "base": 45.0, "noise": 5.0},
}


def _sim(sensor: str) -> dict:
    cfg = SENSOR_CONFIG[sensor]
    return {
        "value": round(cfg["base"] + random.gauss(0, cfg["noise"]), 3),
        "unit":  cfg["unit"],
        "ts":    datetime.now(timezone.utc).isoformat(),
    }


def _json(data: dict) -> bytes:
    return json.dumps(data).encode()


# ── Observable Sensor Resource ────────────────────────────────────────────────

class SensorResource(resource.ObservableResource):
    """Observable CoAP resource for a single sensor on a production line."""

    UPDATE_INTERVAL = 5  # seconds

    def __init__(self, line: str, sensor_type: str):
        super().__init__()
        self.line        = line
        self.sensor_type = sensor_type
        self._reading    = _sim(sensor_type)
        # Schedule the periodic update loop on the running event loop.
        # When the resource is constructed there is already a loop running
        # (build_server() is awaited), so ensure_future is safe.
        self._task = asyncio.ensure_future(self._update_loop())

    async def _update_loop(self) -> None:
        """Every UPDATE_INTERVAL seconds, refresh the reading and notify observers."""
        try:
            while True:
                await asyncio.sleep(self.UPDATE_INTERVAL)
                self._reading = _sim(self.sensor_type)
                # updated_state() pushes a notification to every Observe client
                self.updated_state()
        except asyncio.CancelledError:
            pass

    async def render_get(self, request: Message) -> Message:
        return Message(
            code=Code.CONTENT,
            payload=_json(self._reading),
            content_format=JSON_CF,
        )


# ── Actuator Resource ─────────────────────────────────────────────────────────

class ActuatorResource(resource.Resource):
    """A controllable cooling-fan actuator with GET/PUT semantics."""

    VALID_STATES = {"ON", "OFF"}

    def __init__(self):
        super().__init__()
        self._state = "OFF"

    async def render_get(self, request: Message) -> Message:
        body = {"state": self._state, "ts": datetime.now(timezone.utc).isoformat()}
        return Message(code=Code.CONTENT, payload=_json(body), content_format=JSON_CF)

    async def render_put(self, request: Message) -> Message:
        # Parse JSON body
        try:
            data = json.loads(request.payload)
        except (json.JSONDecodeError, ValueError):
            return Message(
                code=Code.BAD_REQUEST,
                payload=b'{"error": "invalid JSON"}',
                content_format=JSON_CF,
            )

        state = data.get("state") if isinstance(data, dict) else None
        if state not in self.VALID_STATES:
            return Message(
                code=Code.BAD_REQUEST,
                payload=b'{"error": "state must be ON or OFF"}',
                content_format=JSON_CF,
            )

        self._state = state
        log.info("Fan state changed -> %s", self._state)
        return Message(
            code=Code.CHANGED,
            payload=_json({"state": self._state}),
            content_format=JSON_CF,
        )


# ── Block-wise Manifest Resource ──────────────────────────────────────────────

class ManifestResource(resource.Resource):
    """Large firmware manifest (>= 3 KB) to demonstrate Block2 transfer."""

    def __init__(self):
        super().__init__()
        self._payload = self._build_manifest()
        log.info("Manifest built: %d bytes", len(self._payload))

    @staticmethod
    def _build_manifest() -> bytes:
        """Produce a deterministic JSON firmware manifest just above 3 KB.

        The assignment requires a JSON document >= 3 KB so that Block2 transfer
        is exercised. A much larger payload (for example ~18 KB) creates many
        Block2 requests and can occasionally exceed the 30-second pytest timeout
        in WSL/Python 3.12 when UDP retransmissions occur. Keeping the payload
        close to the requirement gives a reliable test while still triggering
        Block2 reassembly.
        """
        modules = [
            "boot", "sensor-temp", "sensor-vib", "sensor-power",
            "mqtt-stack", "coap-stack", "actuator-fan", "watchdog",
        ]
        entries = []
        for i in range(12):
            mod = modules[i % len(modules)]
            ver = f"1.{i // 4}.{i % 4}"
            payload = f"{mod}-{ver}".encode()
            checksum = hashlib.sha256(payload).hexdigest()
            entries.append({
                "id": f"fw-{i:04d}",
                "module": mod,
                "line": f"line{1 + (i % 2)}",
                "version": ver,
                "size_bytes": 4096 + i * 17,
                "sha256": checksum,
                "url": f"https://updates.smartfactory.local/firmware/{mod}-{ver}.bin",
                "released": "2026-03-01T00:00:00Z",
                "mandatory": (i % 5 == 0),
            })

        manifest = {
            "manifest_version": "1.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "vendor": "SmartFactory Inc.",
            "entries": entries,
        }
        body = json.dumps(manifest, separators=(",", ":")).encode("utf-8")

        # Keep padding inside a JSON string so the response remains valid JSON.
        # Add a small margin above 3072 bytes to avoid boundary differences.
        target_size = 3400
        if len(body) < target_size:
            manifest["padding"] = "x" * (target_size - len(body))
            body = json.dumps(manifest, separators=(",", ":")).encode("utf-8")
        return body

    async def render_get(self, request: Message) -> Message:
        return Message(code=Code.CONTENT, payload=self._payload, content_format=JSON_CF)


# ── Resource Tree & Server Setup ──────────────────────────────────────────────

async def build_server() -> aiocoap.Context:
    """Build the resource tree and return a running CoAP server context."""
    root = resource.Site()

    # Sensor resources (two lines × three sensor types)
    for line in ("line1", "line2"):
        for sensor in ("temperature", "vibration", "power"):
            root.add_resource(
                ["factory", line, sensor],
                SensorResource(line, sensor),
            )

    # Actuator
    root.add_resource(["actuator", "line1", "fan"], ActuatorResource())

    # Large manifest for Block2 demonstration
    root.add_resource(["factory", "manifest"], ManifestResource())

    # Resource discovery
    root.add_resource(
        [".well-known", "core"],
        resource.WKCResource(root.get_resources_as_linkheader),
    )

    context = await aiocoap.Context.create_server_context(root)
    return context


async def main() -> None:
    await build_server()
    log.info("CoAP server running on coap://localhost:5683")
    log.info(
        "Resources: /factory/line{1,2}/{temperature,vibration,power}, "
        "/actuator/line1/fan, /factory/manifest"
    )
    await asyncio.get_event_loop().create_future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())
