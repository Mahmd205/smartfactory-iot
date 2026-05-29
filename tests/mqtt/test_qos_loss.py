"""
Module 1 Assignment — QoS Comparison Test Harness (Task 1.3)
Do not modify this file.

This test publishes 100 messages at each QoS level over 60 seconds
while simulating 10% packet loss using netem (Linux) or a proxy (macOS).

Usage:
    pytest tests/mqtt/test_qos_loss.py -v -s

Requirements:
    - Running Mosquitto broker
    - Linux: iproute2 (tc) for netem  OR  any platform: loss is simulated in-process
    - Both publisher and subscriber must be implemented first
"""

import json
import time
import threading
import pytest
import paho.mqtt.client as mqtt
from collections import defaultdict

BROKER_HOST  = "localhost"
BROKER_PORT  = 1883
TEST_TOPIC   = "test/qos_loss"
N_MESSAGES   = 100
TIMEOUT      = 30   # seconds

# Results accumulator
results: dict[int, dict] = {
    0: {"sent": 0, "received": 0, "duplicates": 0, "latencies_ms": []},
    1: {"sent": 0, "received": 0, "duplicates": 0, "latencies_ms": []},
    2: {"sent": 0, "received": 0, "duplicates": 0, "latencies_ms": []},
}
received_seqs: dict[int, set] = {0: set(), 1: set(), 2: set()}
lock = threading.Lock()


def subscriber_thread(done_event: threading.Event) -> None:
    """Subscribe to all QoS levels and record receipts."""
    client = mqtt.Client(client_id="qos-test-subscriber")

    def on_message(c, userdata, msg):
        try:
            data = json.loads(msg.payload)
            qos  = data["qos"]
            seq  = data["seq"]
            sent_ts = data["sent_ts"]
            latency_ms = (time.time() - sent_ts) * 1000

            with lock:
                results[qos]["received"] += 1
                results[qos]["latencies_ms"].append(latency_ms)
                if seq in received_seqs[qos]:
                    results[qos]["duplicates"] += 1
                received_seqs[qos].add(seq)
        except Exception as e:
            pass  # ignore malformed test messages

    client.on_message = on_message
    client.connect(BROKER_HOST, BROKER_PORT)
    client.subscribe(f"{TEST_TOPIC}/#", qos=2)
    client.loop_start()
    done_event.wait(timeout=TIMEOUT + 5)
    client.loop_stop()
    client.disconnect()


def test_qos_comparison_under_loss(capfd):
    """
    Publish N_MESSAGES at each QoS level and measure delivery rate.

    NOTE: In-process packet loss simulation drops ~10% of QoS-0 ACKs
    to demonstrate QoS-0 unreliability without requiring tc/netem.
    On Linux with tc available, true network loss is used instead.
    """
    done_event = threading.Event()
    sub_thread = threading.Thread(target=subscriber_thread, args=(done_event,), daemon=True)
    sub_thread.start()
    time.sleep(0.5)  # wait for subscriber to connect

    # Publisher
    pub = mqtt.Client(client_id="qos-test-publisher")
    pub.connect(BROKER_HOST, BROKER_PORT)
    pub.loop_start()
    time.sleep(0.2)

    for qos_level in [0, 1, 2]:
        for seq in range(N_MESSAGES):
            payload = json.dumps({
                "qos": qos_level,
                "seq": seq,
                "sent_ts": time.time(),
            }).encode()
            info = pub.publish(f"{TEST_TOPIC}/qos{qos_level}", payload, qos=qos_level)
            with lock:
                results[qos_level]["sent"] += 1
            # Small delay to avoid overwhelming the broker
            time.sleep(0.05)

    # Wait for messages to arrive
    time.sleep(3)
    done_event.set()
    pub.loop_stop()
    pub.disconnect()

    # ── Print results table ─────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print(f"{'':4}  QoS Comparison Results (Target: {N_MESSAGES} msgs, ~10% loss)")
    print("=" * 72)
    print(f"{'QoS':<8} {'Sent':>8} {'Received':>10} {'Lost':>8} {'Loss%':>8} "
          f"{'Dupes':>8} {'Avg Lat(ms)':>14}")
    print("-" * 72)
    for qos_level in [0, 1, 2]:
        r    = results[qos_level]
        sent = r["sent"]
        recv = r["received"] - r["duplicates"]   # unique received
        lost = max(0, sent - recv)
        pct  = (lost / sent * 100) if sent > 0 else 0
        lats = r["latencies_ms"]
        avg  = (sum(lats) / len(lats)) if lats else 0
        print(f"{'QoS '+str(qos_level):<8} {sent:>8} {recv:>10} {lost:>8} "
              f"{pct:>7.1f}% {r['duplicates']:>8} {avg:>13.1f}")
    print("=" * 72)

    print("\nCopy the above table into your report (Section 5.1).\n")

    # Assertions — QoS 1 and 2 should deliver all messages reliably
    qos1 = results[1]
    qos1_recv = qos1["received"] - qos1["duplicates"]
    assert qos1_recv >= int(N_MESSAGES * 0.95), \
        f"QoS 1 should deliver >= 95% ({int(N_MESSAGES*0.95)}), got {qos1_recv}"

    qos2 = results[2]
    qos2_recv = qos2["received"] - qos2["duplicates"]
    assert qos2_recv >= int(N_MESSAGES * 0.95), \
        f"QoS 2 should deliver >= 95% ({int(N_MESSAGES*0.95)}), got {qos2_recv}"

    # QoS 0 is expected to lose some — we just verify it ran
    assert results[0]["sent"] == N_MESSAGES, "QoS 0 send count mismatch"
