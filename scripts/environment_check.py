#!/usr/bin/env python3
"""
SmartFactory step 01 - environment check.

Run from the project root:
    python scripts/environment_check.py

This file replaces the old shell helper checks. It does not modify your system.
It only checks whether the services/tools needed for the assignment are ready.
"""

from __future__ import annotations

import asyncio
import importlib.util
import shutil
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def ok(label: str, detail: str = "") -> None:
    print(f"[OK]   {label}{': ' + detail if detail else ''}")


def warn(label: str, detail: str = "") -> None:
    print(f"[WARN] {label}{': ' + detail if detail else ''}")


def fail(label: str, detail: str = "") -> None:
    print(f"[FAIL] {label}{': ' + detail if detail else ''}")


def command_version(cmd: list[str]) -> str | None:
    try:
        out = subprocess.run(cmd, text=True, capture_output=True, timeout=10)
    except Exception:
        return None
    text = (out.stdout or out.stderr or "").strip().splitlines()
    return text[0] if text else None


def port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    """TCP reachability check. Use it for Mosquitto only. CoAP is UDP."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


async def _coap_probe_once(timeout: float = 1.0) -> bool:
    import aiocoap
    from aiocoap import Code, Message
    from aiocoap.numbers.types import Type

    ctx = await aiocoap.Context.create_client_context()
    try:
        msg = Message(code=Code.GET, uri="coap://localhost/factory/line1/power", mtype=Type.CON)
        await asyncio.wait_for(ctx.request(msg).response, timeout=timeout)
        return True
    except Exception:
        return False
    finally:
        await ctx.shutdown()


def coap_reachable(timeout: float = 1.0) -> bool:
    """Real CoAP readiness check. CoAP uses UDP, so TCP port checks are wrong here."""
    if importlib.util.find_spec("aiocoap") is None:
        return False
    try:
        return asyncio.run(_coap_probe_once(timeout=timeout))
    except Exception:
        return False


def main() -> int:
    print("SmartFactory IoT - Step 01 Environment Check")
    print(f"Project root: {ROOT}")
    print()

    print("1) Python")
    print(f"   Executable: {sys.executable}")
    version = sys.version_info
    if version >= (3, 10):
        ok("Python version", f"{version.major}.{version.minor}.{version.micro}")
    else:
        fail("Python version", "use Python 3.10 or newer")

    print("\n2) Python packages")
    packages = {
        "paho.mqtt.client": "paho-mqtt",
        "aiocoap": "aiocoap[all]",
        "pytest": "pytest",
    }
    for module, package in packages.items():
        if importlib.util.find_spec(module.split(".")[0]):
            ok(package)
        else:
            fail(package, f"install with: python -m pip install {package}")

    print("\n3) External tools")
    for exe, ver_cmd in {
        "docker": ["docker", "--version"],
        "tshark": ["tshark", "--version"],
    }.items():
        if shutil.which(exe):
            ok(exe, command_version(ver_cmd) or "installed")
        else:
            warn(exe, "not found on PATH")

    print("\n4) Local services")
    if port_open("localhost", 1883):
        ok("MQTT broker", "localhost:1883 is reachable")
    else:
        fail("MQTT broker", "start it with: docker compose up -d mosquitto")

    if coap_reachable(timeout=1.0):
        ok("CoAP server", "responded to coap://localhost/factory/line1/power")
    else:
        warn("CoAP server", "not running yet; QoS/capture scripts can auto-start it")

    print("\n5) Project files")
    for rel in [
        "src/mqtt/publisher.py",
        "src/mqtt/subscriber.py",
        "src/coap/server.py",
        "src/coap/observer.py",
        "report/comparison_report.md",
        "report/packet_analysis.md",
    ]:
        path = ROOT / rel
        if path.exists():
            ok(rel)
        else:
            fail(rel, "missing")

    print("\nNext step:")
    print("  python scripts/generate_qos_table.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
