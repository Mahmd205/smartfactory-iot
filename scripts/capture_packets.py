#!/usr/bin/env python3
"""
SmartFactory step 03 - self-contained packet capture.

Run from the project root:
    python scripts/capture_packets.py

This replaces scripts/capture.sh. It starts tshark directly from Python, auto-starts the CoAP server when needed, runs
small MQTT and CoAP traffic generators, then writes:
    captures/mqtt.pcap
    captures/coap.pcap

Linux/WSL default interface is "lo". On Windows, run `tshark -D` and pass the
loopback interface number/name, for example:
    python scripts/capture_packets.py --interface 5
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAPTURES = ROOT / "captures"
MQTT_PCAP = CAPTURES / "mqtt.pcap"
COAP_PCAP = CAPTURES / "coap.pcap"


def port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    """TCP reachability check. Use it for MQTT only; CoAP is UDP."""
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


def ensure_mqtt_broker() -> bool:
    """Make sure Mosquitto is reachable. If not, try `docker compose up -d mosquitto`.

    This is still step-by-step Python, not a shell script. It simply calls Docker
    directly so packet capture is less likely to produce an empty MQTT pcap.
    """
    if port_open("localhost", 1883, timeout=0.8):
        print("[OK] MQTT broker already reachable at localhost:1883")
        return True

    compose_file = ROOT / "docker-compose.yml"
    docker = shutil.which("docker")
    if docker and compose_file.exists():
        print("MQTT broker is not reachable. Trying to start Mosquitto with Docker Compose ...")
        cmd = [docker, "compose", "up", "-d", "mosquitto"]
        out = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
        if out.returncode != 0:
            print("[WARN] Docker Compose could not start Mosquitto.")
            if out.stderr.strip():
                print(out.stderr.strip())
        else:
            if out.stdout.strip():
                print(out.stdout.strip())
            deadline = time.time() + 20
            while time.time() < deadline:
                if port_open("localhost", 1883, timeout=0.8):
                    print("[OK] MQTT broker ready at localhost:1883")
                    return True
                time.sleep(0.5)

    print("[WARN] MQTT broker is still not reachable at localhost:1883.")
    print("       Run this manually, then re-run capture:")
    print("       docker compose up -d mosquitto")
    return False


def default_interface() -> str:
    system = platform.system().lower()
    if system == "linux":
        return "lo"
    # On Windows/macOS, tshark interface numbers are more reliable than names.
    return "lo"


def require_tshark() -> None:
    if shutil.which("tshark") is None:
        raise SystemExit(
            "tshark is not found on PATH. Install Wireshark/tshark first.\n"
            "Ubuntu/WSL: sudo apt install -y tshark\n"
            "Windows: install Wireshark + Npcap, then open a new terminal."
        )


def start_tshark(interface: str, capture_filter: str, outfile: Path, duration: int) -> subprocess.Popen:
    outfile.parent.mkdir(parents=True, exist_ok=True)
    if outfile.exists():
        outfile.unlink()
    cmd = [
        "tshark",
        "-i", interface,
        "-f", capture_filter,
        "-a", f"duration:{duration}",
        "-w", str(outfile),
    ]
    print("Starting capture:", " ".join(cmd))
    return subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def stop_process(proc: subprocess.Popen | None, name: str) -> None:
    if proc is None or proc.poll() is not None:
        return
    print(f"Stopping {name} ...")
    try:
        if os.name == "nt":
            proc.terminate()
        else:
            proc.send_signal(signal.SIGINT)
        proc.wait(timeout=5)
    except Exception:
        proc.kill()


def start_coap_server() -> subprocess.Popen | None:
    if coap_reachable(timeout=0.8):
        print("[OK] CoAP server already responding on coap://localhost:5683")
        return None
    print("Starting CoAP server ...")
    proc = subprocess.Popen(
        [sys.executable, "-m", "src.coap.server"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.time() + 10
    while time.time() < deadline:
        if proc.poll() is not None:
            print("[WARN] CoAP server exited early")
            return proc
        if coap_reachable(timeout=0.8):
            print("[OK] CoAP server ready and responding to a probe GET")
            return proc
        time.sleep(0.25)
    print("[WARN] CoAP server did not respond within 10 seconds")
    return proc


def start_mqtt_publisher() -> subprocess.Popen | None:
    if not ensure_mqtt_broker():
        print("[WARN] MQTT capture may be empty because the broker is not running.")
        return None
    print("Starting MQTT publisher traffic ...")
    return subprocess.Popen(
        [sys.executable, "-m", "src.mqtt.publisher"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


async def generate_coap_traffic() -> None:
    if importlib.util.find_spec("aiocoap") is None:
        print("[WARN] aiocoap not installed; skipping CoAP traffic generation")
        return
    import aiocoap
    from aiocoap import Code, Message
    from aiocoap.numbers.types import Type

    print("Generating CoAP traffic: CON GET, Observe, manifest GET, actuator PUT ...")
    ctx = await aiocoap.Context.create_client_context()

    # 1) Direct CON GET for packet-analysis request/response pair.
    try:
        req = Message(code=Code.GET, uri="coap://localhost/factory/line1/temperature", mtype=Type.CON)
        await asyncio.wait_for(ctx.request(req).response, timeout=3)
    except Exception as exc:
        print(f"[WARN] direct CoAP GET failed: {exc}")

    # 2) Observe subscription long enough to capture at least one notification.
    try:
        obs = Message(code=Code.GET, uri="coap://localhost/factory/line2/temperature", mtype=Type.CON)
        obs.opt.observe = 0
        request = ctx.request(obs)
        await asyncio.wait_for(request.response, timeout=3)
        # The server updates observable sensor values every 5 seconds.
        # Wait for at least one real notification after the initial response.
        notifications = 0
        deadline = time.time() + 12
        async for notification in request.observation:
            notifications += 1
            print(f"  observed notification received #{notifications}")
            if notifications >= 1 or time.time() >= deadline:
                request.observation.cancel()
                break
    except Exception as exc:
        print(f"[WARN] observe traffic failed: {exc}")

    # 3) Large manifest GET to ensure Block2 packets exist for report discussion.
    try:
        manifest = Message(code=Code.GET, uri="coap://localhost/factory/manifest", mtype=Type.CON)
        await asyncio.wait_for(ctx.request(manifest).response, timeout=8)
    except Exception as exc:
        print(f"[WARN] manifest GET failed: {exc}")

    # 4) Actuator PUT for extra CoAP evidence.
    try:
        put = Message(
            code=Code.PUT,
            uri="coap://localhost/actuator/line1/fan",
            mtype=Type.CON,
            payload=b'{"state":"ON"}',
            content_format=50,
        )
        await asyncio.wait_for(ctx.request(put).response, timeout=3)
    except Exception as exc:
        print(f"[WARN] actuator PUT failed: {exc}")

    await ctx.shutdown()


def pcap_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture MQTT and CoAP packets without a shell script.")
    parser.add_argument("--interface", default=default_interface(), help="tshark interface, e.g., lo or an interface number from tshark -D")
    parser.add_argument("--duration", type=int, default=35, help="capture duration in seconds")
    parser.add_argument("--no-auto-traffic", action="store_true", help="capture only; do not start publisher/server/client traffic")
    args = parser.parse_args()

    require_tshark()
    CAPTURES.mkdir(exist_ok=True)

    print("SmartFactory packet capture")
    print(f"  interface={args.interface}")
    print(f"  duration={args.duration}s")
    print()

    coap_server = None
    mqtt_publisher = None
    mqtt_cap = start_tshark(args.interface, "tcp port 1883", MQTT_PCAP, args.duration)
    coap_cap = start_tshark(args.interface, "udp port 5683", COAP_PCAP, args.duration)

    try:
        time.sleep(2)
        if not args.no_auto_traffic:
            coap_server = start_coap_server()
            mqtt_publisher = start_mqtt_publisher()
            time.sleep(3)
            asyncio.run(generate_coap_traffic())
            # Keep publisher alive long enough to capture multiple QoS 1 packets.
            time.sleep(max(3, args.duration - 18))
        mqtt_cap.wait(timeout=args.duration + 10)
        coap_cap.wait(timeout=args.duration + 10)
    finally:
        stop_process(mqtt_publisher, "MQTT publisher")
        stop_process(coap_server, "CoAP server")
        for proc, name in [(mqtt_cap, "MQTT tshark"), (coap_cap, "CoAP tshark")]:
            if proc.poll() is None:
                proc.terminate()
            out, err = proc.communicate(timeout=5)
            if proc.returncode not in (0, None):
                print(f"[WARN] {name} returned {proc.returncode}")
                if err.strip():
                    print(err.strip())

    print("\nCapture files:")
    print(f"  {MQTT_PCAP.relative_to(ROOT)}  {pcap_size(MQTT_PCAP)} bytes")
    print(f"  {COAP_PCAP.relative_to(ROOT)}  {pcap_size(COAP_PCAP)} bytes")

    if pcap_size(MQTT_PCAP) == 0 or pcap_size(COAP_PCAP) == 0:
        print("\n[WARN] One capture file is empty. Check the interface name with: tshark -D")
    else:
        print("\n[OK] captures created")

    # Quick sanity check: confirm payload packets exist before analysis.
    def count_packets(pcap: Path, display_filter: str) -> int:
        try:
            out = subprocess.run(
                ["tshark", "-r", str(pcap), "-Y", display_filter, "-T", "fields", "-e", "frame.number"],
                cwd=ROOT, text=True, capture_output=True, timeout=10,
            )
            if out.returncode == 0:
                return len([x for x in out.stdout.splitlines() if x.strip()])
        except Exception:
            pass
        return 0

    mqtt_payloads = count_packets(MQTT_PCAP, "tcp.port == 1883 && tcp.payload")
    coap_payloads = count_packets(COAP_PCAP, "udp.port == 5683 && udp.payload")
    print(f"  MQTT TCP payload packets: {mqtt_payloads}")
    print(f"  CoAP UDP payload packets: {coap_payloads}")
    if mqtt_payloads == 0:
        print("[WARN] MQTT capture has no TCP payloads. Check Mosquitto and the capture interface.")
    if coap_payloads == 0:
        print("[WARN] CoAP capture has no UDP payloads. Check the CoAP server and the capture interface.")

    print("\nNext step:")
    print("  python scripts/analyze_packets.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
