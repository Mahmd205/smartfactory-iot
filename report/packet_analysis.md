# Packet Analysis Report

This report was generated from live packet captures using:

```text
python scripts/capture_packets.py
python scripts/analyze_packets.py
```

The values below are extracted from `captures/mqtt.pcap` and `captures/coap.pcap` using `tshark` and raw MQTT/CoAP payload parsing. AMQP is skipped because the provided assignment version says to ignore AMQP.

## 4.2 MQTT Packet Annotation

### 1) CONNECT packet

| Field | Observed value | Explanation |
|---|---:|---|
| Frame type byte | `0x10` = `00010000` | Packet type `0001` = CONNECT; flags `0000` |
| Remaining length | `69` | MQTT variable header + payload length |
| Protocol name bytes | `00 04 4d 51 54 54` | Length `00 04` followed by ASCII `MQTT` |
| Protocol name | `MQTT` | MQTT protocol name |
| Protocol version byte | `4` | MQTT 3.1.1 uses version 4 |
| Connect flags byte | `0x2c` = `00101100` | Expanded below |
| Keep-alive value | `60` seconds | Client keep-alive interval |
| Client ID | `smartfactory-publisher-001` | Publisher client identifier |

**CONNECT flags expansion:**

| Bit | Name | Value | Meaning |
|---:|---|---:|---|
| 7 | Username flag | `0` | not set |
| 6 | Password flag | `0` | not set |
| 5 | Will retain | `1` | retain Last Will |
| 4-3 | Will QoS | `01` | QoS 1 |
| 2 | Will flag | `1` | Last Will present |
| 1 | Clean session | `0` | persistent session |
| 0 | Reserved | `0` | must be 0 |

### 2) QoS 1 PUBLISH packet

| Field | Observed value | Explanation |
|---|---:|---|
| Fixed header byte | `0x32` = `00110010` | Type `0011` = PUBLISH; QoS bits should be `01` |
| Remaining length | `159` | MQTT remaining length field |
| Topic length field | `25` | Number of topic-string bytes |
| Topic string bytes | `66 61 63 74 6f 72 79 2f 6c 69 6e 65 31 2f 74 65 6d 70 65 72 61 74 75 72 65` | UTF-8 topic bytes |
| Topic string | `factory/line1/temperature` | Temperature telemetry topic using `factory/{line}/temperature` |
| Packet Identifier | `3` | Required for QoS 1 |
| Payload | `{"line": "line1", "sensor": "temperature", "value": 69.89, "unit": "C", "timestamp": "2026-05-29T03:15:37.053821+00:00", "seq": 1}` | JSON telemetry payload |

**PUBLISH fixed-header byte expansion:**

| Bits | Value | Meaning |
|---|---:|---|
| 7-4 | `0011` | MQTT packet type = PUBLISH (`0011`) |
| 3 | `0` | DUP flag |
| 2-1 | `01` | QoS level = 1 when value is `01` |
| 0 | `0` | RETAIN flag |

### 3) Corresponding PUBACK

| Field | Observed value | Explanation |
|---|---:|---|
| Fixed header byte | `0x40` = `01000000` | Packet type `0100` = PUBACK |
| Remaining length | `2` | PUBACK variable header length is 2 bytes |
| Packet Identifier | `3` | Must match PUBLISH Packet Identifier |

**Packet-Identifier match:** PUBLISH `3` vs PUBACK `3` → **YES**.

## 4.3 CoAP Packet Annotation

### 4) CON GET request

| Field | Observed value | Explanation |
|---|---:|---|
| Header byte 0 | `0x42` = `01000010` | Version + Type + Token Length |
| Version bits 7-6 | `01` | Version `1` |
| Type bits 5-4 | `00` | Type `0` = CON |
| TKL bits 3-0 | `0010` | Token length `2` bytes |
| Code | `1` | `0.01` = GET |
| Message ID | `31195` | Request identifier |
| Token bytes | `f0:46` | Used to match response |
| Uri-Path | `/factory/line1/temperature` | Requested resource path |

**Header byte 0 expansion:**

| Bit 7 | Bit 6 | Bit 5 | Bit 4 | Bit 3 | Bit 2 | Bit 1 | Bit 0 |
|---:|---:|---:|---:|---:|---:|---:|---:|
| `0` | `1` | `0` | `0` | `0` | `0` | `1` | `0` |

**Uri-Path option delta encoding:**

| Option index | Uri-Path segment | Delta | Cumulative option number |
|---:|---|---:|---:|
| 1 | `factory` | `11` | `11` |
| 2 | `line1` | `0` | `11` |
| 3 | `temperature` | `0` | `11` |

### 5) ACK 2.05 Content response

| Field | Observed value | Explanation |
|---|---:|---|
| Version | `1` | CoAP version |
| Type | `2` | Type `2` = ACK |
| Token length | `2` | Response token length |
| Code | `69` | `69` = 2.05 Content |
| Message ID | `31195` | Should match confirmable request in piggybacked ACK |
| Token | `f0:46` | Token match with request: **YES** |
| Content-Format option | `50` | `50` = application/json |
| Payload marker | `0xFF` | `0xFF` separates options from payload |
| Payload | `{"value": 68.077, "unit": "C", "ts": "2026-05-29T03:15:32.646167+00:00"}` | JSON body |
| Payload length | `72` | Bytes |

### 6) Observe notification

| Field | Observed value | Explanation |
|---|---:|---|
| Observe option number | `6` | Observe is option 6 |
| Observe sequence value | `1` | Monotonically increasing notification sequence |
| Message type | `0` | Notification CoAP type |
| Response code | `69` | `2.05` |
| Token | `f0:47` | Identifies observe relationship |

## 4.4 AMQP Frame Annotation

AMQP is intentionally omitted because the assignment copy used for this project marks AMQP sections as ignored. No AMQP capture is required for this submission version.
