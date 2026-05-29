#!/usr/bin/env python3
"""
SmartFactory step 02 - self-contained QoS table generator.

Run from the project root after Mosquitto is running:
    python scripts/generate_qos_table.py

Optional Linux/WSL packet-loss mode, no shell script required:
    python scripts/generate_qos_table.py --apply-netem --loss-percent 10

What it does:
  1. Runs its own MQTT QoS 0/1/2 measurement. It does not parse pytest output.
  2. Starts the CoAP server automatically if it is not already running.
  3. Measures CoAP NON and CON GET requests.
  4. Counts duplicates:
       - MQTT: repeated application sequence numbers per QoS level.
       - CoAP: repeated CoAP response Message IDs visible to the client.
  5. Writes:
       report/qos_measurements.json
       report/qos_table.md
       report/comparison_report.md   (Section 5.1 table replaced)

Important: MQTT runs over TCP, so ordinary packet loss on loopback may be
recovered by TCP before MQTT sees it. If you use --apply-netem, mention in the
report that packet loss was injected at the Linux loopback interface.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections.abc import MutableMapping
from pathlib import Path
from statistics import mean
from typing import Any

try:
    import paho.mqtt.client as mqtt
except ImportError:  # pragma: no cover - user environment check
    mqtt = None

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "report" / "comparison_report.md"
OUT_JSON = ROOT / "report" / "qos_measurements.json"
OUT_MD = ROOT / "report" / "qos_table.md"


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def tcp_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    """TCP check. Use this for Mosquitto only. CoAP is UDP, so do not use this for CoAP."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def reason_code_is_success(reason_code: Any) -> bool:
    try:
        return int(reason_code) == 0
    except Exception:
        return str(reason_code).lower() in {"0", "success", "connection accepted"}


# ---------------------------------------------------------------------------
# Linux/WSL packet loss helper, without shell scripts
# ---------------------------------------------------------------------------

class NetemGuard:
    """Apply and remove Linux loopback packet loss without a shell script."""

    def __init__(self, enabled: bool, percent: float, interface: str = "lo") -> None:
        self.enabled = enabled
        self.percent = percent
        self.interface = interface
        self.applied = False
        self.note = "netem disabled"

    def _run(self, args: list[str]) -> subprocess.CompletedProcess:
        prefix: list[str] = []
        if hasattr(os, "geteuid") and os.geteuid() != 0:
            prefix = ["sudo"]
        return subprocess.run(prefix + args, text=True, capture_output=True)

    def __enter__(self) -> "NetemGuard":
        if not self.enabled:
            return self
        if platform.system().lower() != "linux":
            self.note = "netem requested but this is not Linux/WSL; skipped"
            print(f"[WARN] {self.note}")
            return self
        if shutil.which("tc") is None:
            self.note = "netem requested but tc/iproute2 is not installed; skipped"
            print(f"[WARN] {self.note}")
            return self

        # Remove an old qdisc if it exists, then add the requested loss.
        self._run(["tc", "qdisc", "del", "dev", self.interface, "root"])
        add = self._run([
            "tc", "qdisc", "add", "dev", self.interface, "root",
            "netem", "loss", f"{self.percent}%",
        ])
        if add.returncode == 0:
            self.applied = True
            self.note = f"netem enabled on {self.interface}: {self.percent}% packet loss"
            print(f"[OK] {self.note}")
        else:
            self.note = "netem failed; continuing without OS-level packet loss"
            print(f"[WARN] {self.note}")
            if add.stderr.strip():
                print(add.stderr.strip())
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.applied:
            self._run(["tc", "qdisc", "del", "dev", self.interface, "root"])
            print(f"[OK] removed netem from {self.interface}")


# ---------------------------------------------------------------------------
# MQTT measurement
# ---------------------------------------------------------------------------

def run_mqtt_measurement(host: str, port: int, messages_per_qos: int, duration: float) -> dict[str, Any]:
    if mqtt is None:
        raise SystemExit("paho-mqtt is not installed. Run: python -m pip install -r requirements.txt")
    if not tcp_port_open(host, port):
        raise SystemExit(
            f"MQTT broker is not reachable at {host}:{port}.\n"
            "Start it with: docker compose up -d mosquitto"
        )

    print("\nMQTT measurement")
    print(f"  broker={host}:{port}, messages_per_qos={messages_per_qos}, duration={duration:.1f}s")
    print("  duplicate rule: repeated application sequence number per QoS level")

    topic_root = f"qos/selfcontained/{int(time.time())}"
    lock = threading.Lock()
    connected_sub = threading.Event()
    connected_pub = threading.Event()
    results: dict[int, MutableMapping[str, Any]] = {
        q: {
            "sent": 0,
            "received_total": 0,
            "duplicates": 0,
            "mqtt_dup_flag_count": 0,
            "latencies_ms": [],
            "received_seqs": set(),
        }
        for q in (0, 1, 2)
    }

    def on_sub_connect(client, userdata, flags, reason_code, properties=None):
        if reason_code_is_success(reason_code):
            client.subscribe(f"{topic_root}/#", qos=2)
            connected_sub.set()
        else:
            print(f"[WARN] subscriber connect failed: {reason_code}")

    def on_pub_connect(client, userdata, flags, reason_code, properties=None):
        if reason_code_is_success(reason_code):
            connected_pub.set()
        else:
            print(f"[WARN] publisher connect failed: {reason_code}")

    def on_message(client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
            qos_level = int(payload["qos"])
            seq = int(payload["seq"])
            sent_ts = float(payload["sent_ts"])
        except Exception:
            return
        latency_ms = (time.time() - sent_ts) * 1000.0
        with lock:
            r = results[qos_level]
            r["received_total"] += 1
            if getattr(msg, "dup", False):
                r["mqtt_dup_flag_count"] += 1
            if seq in r["received_seqs"]:
                r["duplicates"] += 1
            else:
                r["received_seqs"].add(seq)
            r["latencies_ms"].append(latency_ms)

    # clean_session=True is fine here because this is only a measurement client.
    sub = mqtt.Client(client_id=f"sf-qos-sub-{int(time.time())}", clean_session=True)
    sub.on_connect = on_sub_connect
    sub.on_message = on_message
    sub.connect(host, port, keepalive=60)
    sub.loop_start()
    if not connected_sub.wait(5):
        raise SystemExit("Subscriber could not connect/subscribe within 5 seconds.")

    pub = mqtt.Client(client_id=f"sf-qos-pub-{int(time.time())}", clean_session=True)
    pub.on_connect = on_pub_connect
    pub.connect(host, port, keepalive=60)
    pub.loop_start()
    if not connected_pub.wait(5):
        raise SystemExit("Publisher could not connect within 5 seconds.")

    total = messages_per_qos * 3
    interval = max(0.001, duration / total)
    started = time.time()
    for seq in range(messages_per_qos):
        for qos_level in (0, 1, 2):
            payload = json.dumps({
                "qos": qos_level,
                "seq": seq,
                "sent_ts": time.time(),
            })
            info = pub.publish(f"{topic_root}/qos{qos_level}", payload, qos=qos_level)
            if qos_level in (1, 2):
                info.wait_for_publish(timeout=2)
            with lock:
                results[qos_level]["sent"] += 1
            elapsed = time.time() - started
            target = (seq * 3 + qos_level + 1) * interval
            sleep_for = target - elapsed
            if sleep_for > 0:
                time.sleep(sleep_for)

    time.sleep(5)
    pub.loop_stop()
    sub.loop_stop()
    pub.disconnect()
    sub.disconnect()

    summarized: dict[str, Any] = {}
    for qos_level in (0, 1, 2):
        r = results[qos_level]
        unique_recv = len(r["received_seqs"])
        lost = max(0, r["sent"] - unique_recv)
        summarized[str(qos_level)] = {
            "sent": int(r["sent"]),
            "received": int(unique_recv),
            "received_total_including_duplicates": int(r["received_total"]),
            "lost": int(lost),
            "lost_percent": (lost / r["sent"] * 100.0) if r["sent"] else 0.0,
            "duplicates": int(r["duplicates"]),
            "mqtt_dup_flag_count": int(r["mqtt_dup_flag_count"]),
            "avg_latency_ms": mean(r["latencies_ms"]) if r["latencies_ms"] else 0.0,
        }
        print_row(f"MQTT QoS {qos_level}", summarized[str(qos_level)])
    return summarized


# ---------------------------------------------------------------------------
# CoAP server auto-start and measurement
# ---------------------------------------------------------------------------

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


def start_coap_server(auto_start: bool = True) -> subprocess.Popen | None:
    if coap_reachable(timeout=0.8):
        print("[OK] CoAP server already responding on coap://localhost:5683")
        return None
    if not auto_start:
        print("[WARN] CoAP server is not responding and --no-auto-coap-server was used")
        return None

    print("Starting CoAP server temporarily ...")
    proc = subprocess.Popen(
        [sys.executable, "-m", "src.coap.server"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    deadline = time.time() + 10
    while time.time() < deadline:
        if proc.poll() is not None:
            print("[WARN] CoAP server exited early")
            return proc
        if coap_reachable(timeout=0.8):
            print("[OK] CoAP server started and responded to a probe GET")
            return proc
        time.sleep(0.25)
    print("[WARN] CoAP server did not respond within 10 seconds")
    return proc


async def _coap_round(n: int, confirmable: bool) -> dict[str, Any]:
    import aiocoap
    from aiocoap import Code, Message
    from aiocoap.numbers.types import Type

    ctx = await aiocoap.Context.create_client_context()
    sent = 0
    received_total = 0
    duplicates = 0
    latencies: list[float] = []
    seen_response_mids: set[int] = set()
    missing_mid_count = 0

    for seq in range(n):
        # Query parameter gives each application request a unique URI while the server still serves the same resource.
        msg = Message(
            code=Code.GET,
            uri=f"coap://localhost/factory/line1/power?seq={seq}",
            mtype=Type.CON if confirmable else Type.NON,
        )
        sent += 1
        t0 = time.time()
        try:
            response = await asyncio.wait_for(ctx.request(msg).response, timeout=2.0)
            received_total += 1
            latencies.append((time.time() - t0) * 1000.0)
            mid = getattr(response, "mid", None)
            if mid is None:
                missing_mid_count += 1
            elif mid in seen_response_mids:
                duplicates += 1
            else:
                seen_response_mids.add(mid)
        except Exception:
            pass
        await asyncio.sleep(0.05)

    await ctx.shutdown()
    unique_received = received_total - duplicates
    lost = max(0, sent - unique_received)
    return {
        "sent": sent,
        "received": unique_received,
        "received_total_including_duplicates": received_total,
        "lost": lost,
        "lost_percent": (lost / sent * 100.0) if sent else 0.0,
        "duplicates": duplicates,
        "duplicate_rule": "same CoAP response Message ID observed more than once",
        "response_message_ids_observed": len(seen_response_mids),
        "responses_without_mid": missing_mid_count,
        "avg_latency_ms": mean(latencies) if latencies else 0.0,
    }


def run_coap_measurement(n: int, auto_start_server: bool) -> dict[str, Any]:
    print("\nCoAP measurement")
    print("  duplicate rule: repeated response Message ID")
    proc = start_coap_server(auto_start=auto_start_server)
    try:
        if importlib.util.find_spec("aiocoap") is None:
            print("[WARN] aiocoap is not installed; CoAP rows set to n/a")
            return {}
        if not coap_reachable(timeout=1.5):
            print("[WARN] CoAP server is not reachable; CoAP rows set to n/a")
            return {}
        non = asyncio.run(_coap_round(n, confirmable=False))
        con = asyncio.run(_coap_round(n, confirmable=True))
        print_row("CoAP NON", non)
        print_row("CoAP CON", con)
        return {"NON": non, "CON": con}
    except Exception as exc:
        print(f"[WARN] CoAP measurement failed: {exc}")
        return {}
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


# ---------------------------------------------------------------------------
# Report writing
# ---------------------------------------------------------------------------

def fmt_row(label: str, r: dict[str, Any] | None) -> str:
    if not r:
        return f"| {label} | n/a | n/a | n/a | n/a | n/a |"
    return (
        f"| {label} | {r['sent']} | {r['received']} | "
        f"{r['lost_percent']:.1f}% | {r['duplicates']} | {r['avg_latency_ms']:.1f} |"
    )


def print_row(label: str, r: dict[str, Any]) -> None:
    print(
        f"  {label:<12} sent={r['sent']:<4} received={r['received']:<4} "
        f"lost={r['lost_percent']:.1f}% duplicates={r['duplicates']:<3} "
        f"latency={r['avg_latency_ms']:.1f} ms"
    )


def build_table(mqtt_results: dict[str, Any], coap_results: dict[str, Any]) -> str:
    return (
        "| Protocol / QoS | Sent | Received | Lost (%) | Duplicates | Avg Latency (ms) |\n"
        "|----------------|------|----------|----------|------------|------------------|\n"
        f"{fmt_row('MQTT QoS 0', mqtt_results.get('0'))}\n"
        f"{fmt_row('MQTT QoS 1', mqtt_results.get('1'))}\n"
        f"{fmt_row('MQTT QoS 2', mqtt_results.get('2'))}\n"
        f"{fmt_row('CoAP NON', coap_results.get('NON'))}\n"
        f"{fmt_row('CoAP CON', coap_results.get('CON'))}\n"
        "| AMQP (skipped) | - | - | - | - | - |\n"
    )


def replace_section_5_1(table: str, note: str) -> None:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    if not REPORT.exists():
        REPORT.write_text("# SmartFactory Protocol Comparison Report\n\n## 5.1 QoS Comparison Results Table\n\n")
    text = REPORT.read_text()
    note_md = f"\n**Measurement note:** {note}\n\n"
    replacement = "## 5.1 QoS Comparison Results Table\n" + note_md + table + "\n"

    pattern = re.compile(r"## 5\.1 QoS Comparison Results Table\n.*?(?=\n## 5\.2 |\Z)", re.DOTALL)
    if pattern.search(text):
        text = pattern.sub(replacement.rstrip(), text)
    else:
        text += "\n\n" + replacement
    REPORT.write_text(text)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the SmartFactory QoS table without shell scripts.")
    parser.add_argument("--broker", default="localhost")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--messages-per-qos", type=int, default=100)
    parser.add_argument("--duration", type=float, default=60.0, help="MQTT publish window in seconds")
    parser.add_argument("--coap-requests", type=int, default=50)
    parser.add_argument("--no-auto-coap-server", action="store_true", help="do not auto-start src.coap.server")
    parser.add_argument("--apply-netem", action="store_true", help="Linux/WSL only: apply tc netem packet loss to loopback")
    parser.add_argument("--loss-percent", type=float, default=10.0)
    parser.add_argument("--netem-interface", default="lo")
    args = parser.parse_args()

    (ROOT / "report").mkdir(exist_ok=True)

    with NetemGuard(args.apply_netem, args.loss_percent, args.netem_interface) as netem:
        mqtt_results = run_mqtt_measurement(args.broker, args.port, args.messages_per_qos, args.duration)
        coap_results = run_coap_measurement(args.coap_requests, auto_start_server=not args.no_auto_coap_server)

    table = build_table(mqtt_results, coap_results)
    note = (
        f"Self-contained Python measurement; MQTT duration={args.duration:.0f}s; "
        f"messages_per_qos={args.messages_per_qos}; CoAP requests per mode={args.coap_requests}; "
        "duplicates counted as repeated MQTT sequence numbers and repeated CoAP response Message IDs; "
        f"{netem.note}."
    )
    data = {
        "measurement_note": note,
        "mqtt": mqtt_results,
        "coap": coap_results,
    }
    OUT_JSON.write_text(json.dumps(data, indent=2))
    OUT_MD.write_text(table)
    replace_section_5_1(table, note)

    print("\nGenerated table:\n")
    print(table)
    print(f"[OK] saved raw numbers to {OUT_JSON.relative_to(ROOT)}")
    print(f"[OK] saved table to {OUT_MD.relative_to(ROOT)}")
    print(f"[OK] updated {REPORT.relative_to(ROOT)} Section 5.1")
    print("\nNext step:")
    print("  python scripts/capture_packets.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
