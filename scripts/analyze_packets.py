#!/usr/bin/env python3
"""
SmartFactory step 04 - self-contained packet analyzer.

Run from the project root after captures exist:
    python scripts/analyze_packets.py

This script reads:
    captures/mqtt.pcap
    captures/coap.pcap

and writes:
    report/packet_analysis.md
    report/packet_analysis_values.json

The important change in this version is that it parses MQTT and CoAP from the
raw TCP/UDP payload bytes whenever tshark's high-level field names differ
between Wireshark versions. This prevents '(not found)' tables when the packets
exist but tshark exposes fields using different names.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path
from textwrap import dedent
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MQTT_PCAP = ROOT / "captures" / "mqtt.pcap"
COAP_PCAP = ROOT / "captures" / "coap.pcap"
OUT_MD = ROOT / "report" / "packet_analysis.md"
OUT_JSON = ROOT / "report" / "packet_analysis_values.json"


def require_tshark() -> None:
    if shutil.which("tshark") is None:
        raise SystemExit(
            "tshark is not found on PATH. Install Wireshark/tshark first.\n"
            "Ubuntu/WSL: sudo apt install -y tshark\n"
            "Then run: python scripts/capture_packets.py"
        )


def run_tshark_fields(pcap: Path, display_filter: str, fields: list[str]) -> list[list[str]]:
    """Return rows from tshark -T fields. Empty strings are preserved."""
    if not pcap.exists():
        return []
    cmd = ["tshark", "-r", str(pcap), "-Y", display_filter, "-T", "fields"]
    for f in fields:
        cmd += ["-e", f]
    out = subprocess.run(cmd, text=True, capture_output=True)
    if out.returncode != 0:
        print(f"[WARN] tshark failed for {pcap.name} filter={display_filter!r}")
        if out.stderr.strip():
            print(out.stderr.strip())
        return []
    rows: list[list[str]] = []
    for line in out.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < len(fields):
            parts += [""] * (len(fields) - len(parts))
        rows.append(parts)
    return rows


def clean_hex(s: str | None) -> str:
    if not s:
        return ""
    return re.sub(r"[^0-9a-fA-F]", "", str(s))


def bytes_from_hex(s: str | None) -> bytes:
    h = clean_hex(s)
    if not h or len(h) % 2:
        return b""
    try:
        return bytes.fromhex(h)
    except ValueError:
        return b""


def bin8(x: int) -> str:
    return format(x & 0xFF, "08b")


def hex_bytes(data: bytes) -> str:
    return " ".join(f"{b:02x}" for b in data)


def decode_text(data: bytes) -> str:
    try:
        return data.decode("utf-8", errors="replace")
    except Exception:
        return repr(data)


def int_from_bytes(data: bytes) -> int:
    return int.from_bytes(data, "big") if data else 0


# ---------------------------------------------------------------------------
# MQTT raw parser
# ---------------------------------------------------------------------------

def parse_mqtt_remaining_length(data: bytes, pos: int) -> tuple[int | None, int]:
    multiplier = 1
    value = 0
    start = pos
    while pos < len(data):
        encoded = data[pos]
        pos += 1
        value += (encoded & 127) * multiplier
        if (encoded & 128) == 0:
            return value, pos - start
        multiplier *= 128
        if multiplier > 128 * 128 * 128:
            break
    return None, 0


def iter_mqtt_packets_from_segment(segment: bytes) -> list[dict[str, Any]]:
    """Parse all complete MQTT control packets from one TCP payload segment."""
    packets: list[dict[str, Any]] = []
    pos = 0
    while pos + 2 <= len(segment):
        start = pos
        first = segment[pos]
        pos += 1
        rem_len, len_bytes = parse_mqtt_remaining_length(segment, pos)
        if rem_len is None or len_bytes == 0:
            break
        pos += len_bytes
        end = pos + rem_len
        if end > len(segment):
            break
        body = segment[pos:end]
        control_type = first >> 4
        flags = first & 0x0F
        pkt: dict[str, Any] = {
            "type": control_type,
            "flags": flags,
            "first_byte": first,
            "remaining_length": rem_len,
            "raw": segment[start:end],
        }

        try:
            if control_type == 1:  # CONNECT
                p = 0
                proto_len = int_from_bytes(body[p:p + 2]); p += 2
                proto_name = body[p:p + proto_len]; p += proto_len
                version = body[p] if p < len(body) else None; p += 1
                conflags = body[p] if p < len(body) else None; p += 1
                keepalive = int_from_bytes(body[p:p + 2]); p += 2
                client_len = int_from_bytes(body[p:p + 2]); p += 2
                client_id = body[p:p + client_len]
                pkt.update({
                    "protocol_name_bytes": body[0:2 + proto_len],
                    "protocol_name": decode_text(proto_name),
                    "protocol_version": version,
                    "connect_flags": conflags,
                    "keep_alive": keepalive,
                    "client_id": decode_text(client_id),
                })
            elif control_type == 3:  # PUBLISH
                p = 0
                topic_len = int_from_bytes(body[p:p + 2]); p += 2
                topic_bytes = body[p:p + topic_len]; p += topic_len
                qos = (first >> 1) & 0x03
                packet_id = None
                if qos > 0:
                    packet_id = int_from_bytes(body[p:p + 2]); p += 2
                payload = body[p:]
                pkt.update({
                    "topic_length": topic_len,
                    "topic_string_bytes": topic_bytes,
                    "topic_string": decode_text(topic_bytes),
                    "qos": qos,
                    "packet_identifier": packet_id,
                    "payload": decode_text(payload),
                })
            elif control_type == 4:  # PUBACK
                pkt.update({"packet_identifier": int_from_bytes(body[0:2])})
        except Exception as exc:
            pkt["parse_warning"] = str(exc)

        packets.append(pkt)
        pos = end
    return packets


def read_mqtt_packets() -> list[dict[str, Any]]:
    rows = run_tshark_fields(MQTT_PCAP, "tcp.port == 1883 && tcp.payload", ["frame.number", "tcp.payload"])
    packets: list[dict[str, Any]] = []
    for frame_no, payload_hex in rows:
        seg = bytes_from_hex(payload_hex)
        for p in iter_mqtt_packets_from_segment(seg):
            p["frame_number"] = frame_no
            packets.append(p)
    return packets


def analyze_mqtt() -> dict[str, Any]:
    out: dict[str, Any] = {"pcap": str(MQTT_PCAP), "exists": MQTT_PCAP.exists()}
    if not MQTT_PCAP.exists():
        return out

    packets = read_mqtt_packets()
    out["packet_count"] = len(packets)
    out["decoded_types"] = [p.get("type") for p in packets]

    connect = next((p for p in packets if p.get("type") == 1), None)
    if connect:
        flags = int(connect.get("connect_flags") or 0)
        out["connect"] = {
            "frame": connect.get("frame_number", ""),
            "frame_type_byte_hex": f"0x{connect['first_byte']:02x}",
            "frame_type_bits": bin8(connect["first_byte"]),
            "remaining_length": connect.get("remaining_length", "(not found)"),
            "protocol_name_bytes": hex_bytes(connect.get("protocol_name_bytes", b"")),
            "protocol_name": connect.get("protocol_name", "(not found)"),
            "protocol_version": connect.get("protocol_version", "(not found)"),
            "connect_flags_hex": f"0x{flags:02x}",
            "connect_flags_bits": bin8(flags),
            "keep_alive": connect.get("keep_alive", "(not found)"),
            "client_id": connect.get("client_id", "(not found)"),
        }

    # Prefer a real QoS 1 telemetry PUBLISH, not the retained online/status message.
    # Temperature readings use QoS 1 in this assignment, so this gives a JSON
    # sensor payload and a topic such as factory/line1/temperature.
    pub_candidates = [p for p in packets if p.get("type") == 3 and p.get("qos") == 1]
    pub = next((p for p in pub_candidates if str(p.get("topic_string", "")).endswith("/temperature")), None)
    if pub is None:
        pub = next((p for p in pub_candidates if str(p.get("payload", "")).strip().startswith("{")), None)
    if pub is None:
        pub = next(iter(pub_candidates), None)
    if pub:
        msgid = pub.get("packet_identifier")
        out["publish_qos1"] = {
            "frame": pub.get("frame_number", ""),
            "fixed_header_hex": f"0x{pub['first_byte']:02x}",
            "fixed_header_bits": bin8(pub["first_byte"]),
            "remaining_length": pub.get("remaining_length", "(not found)"),
            "topic_length": pub.get("topic_length", "(not found)"),
            "topic_string_bytes": hex_bytes(pub.get("topic_string_bytes", b"")),
            "topic_string": pub.get("topic_string", "(not found)"),
            "packet_identifier": msgid,
            "payload": pub.get("payload", "(not found)"),
        }
        ack = next((p for p in packets if p.get("type") == 4 and p.get("packet_identifier") == msgid), None)
        if ack:
            out["puback"] = {
                "frame": ack.get("frame_number", ""),
                "fixed_header_hex": f"0x{ack['first_byte']:02x}",
                "fixed_header_bits": bin8(ack["first_byte"]),
                "remaining_length": ack.get("remaining_length", "(not found)"),
                "packet_identifier": ack.get("packet_identifier", "(not found)"),
                "matches_publish": True,
            }

    return out


def bits_from_string(bits: str) -> dict[str, str]:
    bits = bits if re.fullmatch(r"[01]{8}", bits or "") else "????????"
    return {
        "b7": bits[0], "b6": bits[1], "b5": bits[2], "b4": bits[3],
        "b3": bits[4], "b2": bits[5], "b1": bits[6], "b0": bits[7],
    }


def render_connect_flags(bits: str) -> str:
    b = bits_from_string(bits)
    will_qos_bits = b["b4"] + b["b3"]
    try:
        will_qos = int(will_qos_bits, 2)
    except Exception:
        will_qos = "?"
    return (
        f"| 7 | Username flag | `{b['b7']}` | {'set' if b['b7'] == '1' else 'not set'} |\n"
        f"| 6 | Password flag | `{b['b6']}` | {'set' if b['b6'] == '1' else 'not set'} |\n"
        f"| 5 | Will retain | `{b['b5']}` | {'retain Last Will' if b['b5'] == '1' else 'do not retain Last Will'} |\n"
        f"| 4-3 | Will QoS | `{will_qos_bits}` | QoS {will_qos} |\n"
        f"| 2 | Will flag | `{b['b2']}` | {'Last Will present' if b['b2'] == '1' else 'no Last Will'} |\n"
        f"| 1 | Clean session | `{b['b1']}` | {'clean session' if b['b1'] == '1' else 'persistent session'} |\n"
        f"| 0 | Reserved | `{b['b0']}` | must be 0 |"
    )


def render_mqtt(d: dict[str, Any]) -> str:
    if not d.get("exists"):
        return "## 4.2 MQTT Packet Annotation\n\n`captures/mqtt.pcap` was not found. Run `python scripts/capture_packets.py` first.\n"

    c = d.get("connect", {})
    p = d.get("publish_qos1", {})
    a = d.get("puback", {})
    ch = c.get("connect_flags_bits", "????????")
    ph = p.get("fixed_header_bits", "????????")
    match_text = "YES" if a.get("matches_publish") else "NO"
    flags_table = render_connect_flags(ch)

    warning = ""
    if not c or not p:
        warning = (
            "> **Warning:** MQTT packets were not decoded from `captures/mqtt.pcap`. "
            "This usually means Mosquitto was not running, the wrong interface was captured, "
            "or the publisher did not start during capture. Re-run `python scripts/capture_packets.py` "
            "after confirming `docker compose up -d mosquitto`.\n\n"
        )

    return f"""## 4.2 MQTT Packet Annotation

{warning}### 1) CONNECT packet

| Field | Observed value | Explanation |
|---|---:|---|
| Frame type byte | `{c.get('frame_type_byte_hex', '(not found)')}` = `{c.get('frame_type_bits', '????????')}` | Packet type `0001` = CONNECT; flags `0000` |
| Remaining length | `{c.get('remaining_length', '(not found)')}` | MQTT variable header + payload length |
| Protocol name bytes | `{c.get('protocol_name_bytes', '(not found)')}` | Length `00 04` followed by ASCII `MQTT` |
| Protocol name | `{c.get('protocol_name', '(not found)')}` | MQTT protocol name |
| Protocol version byte | `{c.get('protocol_version', '(not found)')}` | MQTT 3.1.1 uses version 4 |
| Connect flags byte | `{c.get('connect_flags_hex', '(not found)')}` = `{ch}` | Expanded below |
| Keep-alive value | `{c.get('keep_alive', '(not found)')}` seconds | Client keep-alive interval |
| Client ID | `{c.get('client_id', '(not found)')}` | Publisher client identifier |

**CONNECT flags expansion:**

| Bit | Name | Value | Meaning |
|---:|---|---:|---|
{flags_table}

### 2) QoS 1 PUBLISH packet

| Field | Observed value | Explanation |
|---|---:|---|
| Fixed header byte | `{p.get('fixed_header_hex', '(not found)')}` = `{ph}` | Type `0011` = PUBLISH; QoS bits should be `01` |
| Remaining length | `{p.get('remaining_length', '(not found)')}` | MQTT remaining length field |
| Topic length field | `{p.get('topic_length', '(not found)')}` | Number of topic-string bytes |
| Topic string bytes | `{p.get('topic_string_bytes', '(not found)')}` | UTF-8 topic bytes |
| Topic string | `{p.get('topic_string', '(not found)')}` | Temperature telemetry topic using `factory/{{line}}/temperature` |
| Packet Identifier | `{p.get('packet_identifier', '(not found)')}` | Required for QoS 1 |
| Payload | `{p.get('payload', '(not found)')}` | JSON telemetry payload |

**PUBLISH fixed-header byte expansion:**

| Bits | Value | Meaning |
|---|---:|---|
| 7-4 | `{ph[0:4]}` | MQTT packet type = PUBLISH (`0011`) |
| 3 | `{ph[4:5]}` | DUP flag |
| 2-1 | `{ph[5:7]}` | QoS level = 1 when value is `01` |
| 0 | `{ph[7:8]}` | RETAIN flag |

### 3) Corresponding PUBACK

| Field | Observed value | Explanation |
|---|---:|---|
| Fixed header byte | `{a.get('fixed_header_hex', '(not found)')}` = `{a.get('fixed_header_bits', '????????')}` | Packet type `0100` = PUBACK |
| Remaining length | `{a.get('remaining_length', '(not found)')}` | PUBACK variable header length is 2 bytes |
| Packet Identifier | `{a.get('packet_identifier', '(not found)')}` | Must match PUBLISH Packet Identifier |

**Packet-Identifier match:** PUBLISH `{p.get('packet_identifier', '(not found)')}` vs PUBACK `{a.get('packet_identifier', '(not found)')}` → **{match_text}**.
"""

def parse_extended_option(nibble: int, data: bytes, pos: int) -> tuple[int, int]:
    if nibble < 13:
        return nibble, pos
    if nibble == 13:
        if pos >= len(data):
            return 0, pos
        return data[pos] + 13, pos + 1
    if nibble == 14:
        if pos + 2 > len(data):
            return 0, pos
        return int_from_bytes(data[pos:pos + 2]) + 269, pos + 2
    return 0, pos  # 15 is reserved


def parse_coap_options(data: bytes, pos: int) -> tuple[list[dict[str, Any]], bytes, bool]:
    options: list[dict[str, Any]] = []
    current_number = 0
    payload_marker = False
    while pos < len(data):
        if data[pos] == 0xFF:
            payload_marker = True
            pos += 1
            return options, data[pos:], payload_marker
        header = data[pos]
        pos += 1
        delta_n = header >> 4
        len_n = header & 0x0F
        delta, pos = parse_extended_option(delta_n, data, pos)
        length, pos = parse_extended_option(len_n, data, pos)
        current_number += delta
        value = data[pos:pos + length]
        pos += length
        options.append({
            "number": current_number,
            "delta": delta,
            "length": length,
            "value_bytes": value,
            "value_int": int_from_bytes(value),
            "value_text": decode_text(value),
        })
    return options, b"", payload_marker


def parse_coap_packet(payload: bytes, frame_no: str = "") -> dict[str, Any] | None:
    if len(payload) < 4:
        return None
    b0 = payload[0]
    version = (b0 >> 6) & 0x03
    typ = (b0 >> 4) & 0x03
    tkl = b0 & 0x0F
    code = payload[1]
    mid = int_from_bytes(payload[2:4])
    if tkl > 8 or 4 + tkl > len(payload):
        return None
    token = payload[4:4 + tkl]
    options, body, has_marker = parse_coap_options(payload, 4 + tkl)
    uri_paths = [opt["value_text"] for opt in options if opt["number"] == 11]
    content_formats = [opt["value_int"] for opt in options if opt["number"] == 12]
    observes = [opt["value_int"] for opt in options if opt["number"] == 6]
    return {
        "frame": frame_no,
        "raw": payload,
        "byte0": b0,
        "version": version,
        "type": typ,
        "token_length": tkl,
        "code": code,
        "message_id": mid,
        "token": token,
        "token_hex": ":".join(f"{b:02x}" for b in token),
        "options": options,
        "uri_path": "/" + "/".join(uri_paths) if uri_paths else "",
        "content_format": content_formats[0] if content_formats else None,
        "observe": observes[0] if observes else None,
        "payload_marker": has_marker,
        "payload": body,
    }


def read_coap_packets() -> list[dict[str, Any]]:
    rows = run_tshark_fields(COAP_PCAP, "udp.port == 5683 && udp.payload", ["frame.number", "udp.payload"])
    packets: list[dict[str, Any]] = []
    for frame_no, payload_hex in rows:
        pkt = parse_coap_packet(bytes_from_hex(payload_hex), frame_no)
        if pkt:
            packets.append(pkt)
    return packets


def coap_code_text(code: int) -> str:
    if code == 1:
        return "0.01 GET"
    if code == 2:
        return "0.02 POST"
    if code == 3:
        return "0.03 PUT"
    if code == 4:
        return "0.04 DELETE"
    cls = code >> 5
    detail = code & 0x1F
    return f"{cls}.{detail:02d}"


def analyze_coap() -> dict[str, Any]:
    out: dict[str, Any] = {"pcap": str(COAP_PCAP), "exists": COAP_PCAP.exists()}
    if not COAP_PCAP.exists():
        return out

    packets = read_coap_packets()
    out["packet_count"] = len(packets)

    # Prefer the direct non-observe temperature GET generated by capture_packets.py.
    get_candidates = [p for p in packets if p["type"] == 0 and p["code"] == 1 and p.get("observe") is None]
    if not get_candidates:
        get_candidates = [p for p in packets if p["type"] == 0 and p["code"] == 1]
    if get_candidates:
        g = get_candidates[0]
        uri_opts = [opt for opt in g["options"] if opt["number"] == 11]
        out["con_get"] = {
            "frame": g.get("frame", ""),
            "byte0_hex": f"0x{g['byte0']:02x}",
            "byte0_bits": bin8(g["byte0"]),
            "version": g["version"],
            "type": g["type"],
            "token_length": g["token_length"],
            "code": g["code"],
            "message_id": g["message_id"],
            "token": g["token_hex"],
            "uri_path": g.get("uri_path") or "(not found)",
            "option_numbers": [opt["number"] for opt in uri_opts],
            "option_deltas": [opt["delta"] for opt in uri_opts],
        }
        # Piggybacked ACK response to same MID and token.
        ack = next((p for p in packets if p["type"] == 2 and p["code"] == 69 and p["message_id"] == g["message_id"] and p["token"] == g["token"]), None)
        if not ack:
            ack = next((p for p in packets if p["code"] == 69 and p["token"] == g["token"]), None)
        if ack:
            out["ack_content"] = {
                "frame": ack.get("frame", ""),
                "version": ack["version"],
                "type": ack["type"],
                "token_length": ack["token_length"],
                "code": ack["code"],
                "message_id": ack["message_id"],
                "token": ack["token_hex"],
                "token_matches": ack["token"] == g["token"],
                "content_format": ack["content_format"] if ack["content_format"] is not None else "(not found)",
                "payload_marker": "0xFF" if ack["payload_marker"] else "(not found)",
                "payload": decode_text(ack["payload"]) if ack["payload"] else "(not found)",
                "payload_length": len(ack["payload"]),
            }

    observe_candidates = [p for p in packets if p.get("observe") is not None and p["code"] == 69]
    # Prefer a real notification after initial observe response when available: type CON/NON and 2.05 Content.
    preferred = [p for p in observe_candidates if p["type"] in (0, 1)]
    obs = (preferred or observe_candidates or [None])[0]
    if obs:
        out["observe"] = {
            "frame": obs.get("frame", ""),
            "observe_option_number": 6,
            "observe_sequence": obs.get("observe", "(not found)"),
            "message_type": obs.get("type", "(not found)"),
            "code": obs.get("code", "(not found)"),
            "code_text": coap_code_text(int(obs.get("code", 0))),
            "message_id": obs.get("message_id", "(not found)"),
            "token": obs.get("token_hex", "(not found)"),
        }
    return out


def render_option_delta_table(deltas: list[Any], numbers: list[Any]) -> str:
    if not deltas and not numbers:
        return "| (not found) | (not found) | Re-run capture/analyzer or inspect the Wireshark option tree |"
    rows = []
    for i in range(max(len(deltas), len(numbers))):
        delta = deltas[i] if i < len(deltas) else "(not found)"
        number = numbers[i] if i < len(numbers) else "(not found)"
        rows.append(f"| {i + 1} | `{delta}` | `{number}` |")
    return "\n".join(rows)


def render_coap(d: dict[str, Any]) -> str:
    if not d.get("exists"):
        return "## 4.3 CoAP Packet Annotation\n\n`captures/coap.pcap` was not found. Run `python scripts/capture_packets.py` first.\n"

    g = d.get("con_get", {})
    a = d.get("ack_content", {})
    o = d.get("observe", {})
    b0 = g.get("byte0_bits", "????????")
    token_match = "YES" if a.get("token_matches") else "NO"
    deltas = g.get("option_deltas", [])
    nums = g.get("option_numbers", [])
    option_table = render_option_delta_table(deltas, nums)

    warning = ""
    if not g or not a or not o:
        warning = (
            "> **Warning:** Some CoAP fields were not found. Re-run `python scripts/capture_packets.py` "
            "and make sure `python -m src.coap.server` can answer GET requests.\n\n"
        )

    return f"""## 4.3 CoAP Packet Annotation

{warning}### 4) CON GET request

| Field | Observed value | Explanation |
|---|---:|---|
| Header byte 0 | `{g.get('byte0_hex', '(not found)')}` = `{b0}` | Version + Type + Token Length |
| Version bits 7-6 | `{b0[0:2]}` | Version `{g.get('version', '(not found)')}` |
| Type bits 5-4 | `{b0[2:4]}` | Type `{g.get('type', '(not found)')}` = CON |
| TKL bits 3-0 | `{b0[4:8]}` | Token length `{g.get('token_length', '(not found)')}` bytes |
| Code | `{g.get('code', '(not found)')}` | `0.01` = GET |
| Message ID | `{g.get('message_id', '(not found)')}` | Request identifier |
| Token bytes | `{g.get('token', '(not found)')}` | Used to match response |
| Uri-Path | `{g.get('uri_path', '(not found)')}` | Requested resource path |

**Header byte 0 expansion:**

| Bit 7 | Bit 6 | Bit 5 | Bit 4 | Bit 3 | Bit 2 | Bit 1 | Bit 0 |
|---:|---:|---:|---:|---:|---:|---:|---:|
| `{b0[0]}` | `{b0[1]}` | `{b0[2]}` | `{b0[3]}` | `{b0[4]}` | `{b0[5]}` | `{b0[6]}` | `{b0[7]}` |

**Uri-Path option delta encoding:**

| Option index | Delta | Cumulative option number |
|---:|---:|---:|
{option_table}

### 5) ACK 2.05 Content response

| Field | Observed value | Explanation |
|---|---:|---|
| Version | `{a.get('version', '(not found)')}` | CoAP version |
| Type | `{a.get('type', '(not found)')}` | Type `2` = ACK |
| Token length | `{a.get('token_length', '(not found)')}` | Response token length |
| Code | `{a.get('code', '(not found)')}` | `69` = 2.05 Content |
| Message ID | `{a.get('message_id', '(not found)')}` | Should match confirmable request in piggybacked ACK |
| Token | `{a.get('token', '(not found)')}` | Token match with request: **{token_match}** |
| Content-Format option | `{a.get('content_format', '(not found)')}` | `50` = application/json |
| Payload marker | `{a.get('payload_marker', '(not found)')}` | `0xFF` separates options from payload |
| Payload | `{a.get('payload', '(not found)')}` | JSON body |
| Payload length | `{a.get('payload_length', '(not found)')}` | Bytes |

### 6) Observe notification

| Field | Observed value | Explanation |
|---|---:|---|
| Observe option number | `{o.get('observe_option_number', 6)}` | Observe is option 6 |
| Observe sequence value | `{o.get('observe_sequence', '(not found)')}` | Monotonically increasing notification sequence |
| Message type | `{o.get('message_type', '(not found)')}` | Notification CoAP type |
| Response code | `{o.get('code', '(not found)')}` | `{o.get('code_text', '69 = 2.05 Content')}` |
| Token | `{o.get('token', '(not found)')}` | Identifies observe relationship |
"""

def render_amqp_skipped() -> str:
    return dedent("""
    ## 4.4 AMQP Frame Annotation

    AMQP is intentionally omitted because the assignment copy used for this project marks AMQP sections as ignored. No AMQP capture is required for this submission version.
    """).strip() + "\n"


def render_report(mqtt_d: dict[str, Any], coap_d: dict[str, Any]) -> str:
    return dedent("""
    # Packet Analysis Report

    This report was generated from live packet captures using:

    ```text
    python scripts/capture_packets.py
    python scripts/analyze_packets.py
    ```

    The values below are extracted from `captures/mqtt.pcap` and `captures/coap.pcap` using `tshark` and raw MQTT/CoAP payload parsing. AMQP is skipped because the provided assignment version says to ignore AMQP.
    """).strip() + "\n\n" + render_mqtt(mqtt_d) + "\n" + render_coap(coap_d) + "\n" + render_amqp_skipped()


def main() -> int:
    global MQTT_PCAP, COAP_PCAP
    parser = argparse.ArgumentParser(description="Analyze SmartFactory packet captures without a shell script.")
    parser.add_argument("--mqtt-pcap", type=Path, default=MQTT_PCAP)
    parser.add_argument("--coap-pcap", type=Path, default=COAP_PCAP)
    args = parser.parse_args()

    MQTT_PCAP = args.mqtt_pcap
    COAP_PCAP = args.coap_pcap

    require_tshark()
    mqtt_d = analyze_mqtt()
    coap_d = analyze_coap()

    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text(render_report(mqtt_d, coap_d))
    OUT_JSON.write_text(json.dumps({"mqtt": mqtt_d, "coap": coap_d}, indent=2))

    print(f"[OK] wrote {OUT_MD.relative_to(ROOT)}")
    print(f"[OK] wrote {OUT_JSON.relative_to(ROOT)}")

    missing = []
    if not mqtt_d.get("connect"):
        missing.append("MQTT CONNECT")
    if not mqtt_d.get("publish_qos1"):
        missing.append("MQTT QoS 1 PUBLISH")
    if not mqtt_d.get("puback"):
        missing.append("MQTT PUBACK")
    if not coap_d.get("con_get"):
        missing.append("CoAP CON GET")
    if not coap_d.get("ack_content"):
        missing.append("CoAP ACK 2.05")
    if not coap_d.get("observe"):
        missing.append("CoAP Observe notification")

    if missing:
        print("\n[WARN] These required items were not found:")
        for item in missing:
            print(f"  - {item}")
        print("\nMost common fixes:")
        print("  1. Confirm the right interface: tshark -D")
        print("  2. Start/restart broker: docker compose up -d mosquitto")
        print("  3. Re-run capture: python scripts/capture_packets.py")
    else:
        print("\n[OK] all required MQTT and CoAP packet-analysis fields were found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
