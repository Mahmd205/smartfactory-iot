# SmartFactory IoT - Module 2 Assignment

**Real-Time Data Analytics for IoT** - Graduate Course

This project implements the SmartFactory telemetry pipeline using **MQTT** and **CoAP**.
AMQP/RabbitMQ has been intentionally skipped per the course note for this submission.

---

## What's implemented

| Task | File | Status |
|------|------|--------|
| 1.1 MQTT Publisher | `src/mqtt/publisher.py` | Done |
| 1.2 MQTT Subscriber | `src/mqtt/subscriber.py` | Done |
| 1.3 QoS comparison | `tests/mqtt/test_qos_loss.py` (provided) | Ready to run |
| 2.1 CoAP Server | `src/coap/server.py` | Done |
| 2.2 CoAP Observer | `src/coap/observer.py` | Done |
| Task 3 AMQP | - | **Skipped** |
| Task 4 Packet analysis | `report/packet_analysis.md` | Pending - fill from pcap |
| Task 5 Comparison report | `report/comparison_report.md` | Pending - fill from measured values |

---

## Prerequisites

- **Python 3.10+** (Ubuntu 24 ships with 3.12 - fine)
- **Docker + Docker Compose** (for the Mosquitto broker)
- **tshark / Wireshark** (for Task 4 packet captures)

---

## Quick start on WSL Ubuntu 24

These are the exact commands that work end-to-end on a fresh WSL Ubuntu 24.04 install.

### One-time prerequisites

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git tshark

# Docker - either enable WSL integration in Docker Desktop for Windows,
# OR install natively:
sudo apt install -y docker.io docker-compose-v2
sudo usermod -aG docker $USER         # then EXIT and re-open WSL so the group sticks

# tshark capture permission (so you don't need sudo for captures):
sudo dpkg-reconfigure wireshark-common # answer "Yes" to non-root capture
sudo usermod -aG wireshark $USER       # again, re-open WSL afterwards
```

### Get the project running

```bash
# 1. Unzip / clone the project, then:
cd smartfactory-iot
bash setup.sh                          # creates .venv, installs deps, starts Mosquitto
source .venv/bin/activate              # activate the venv in this shell
```

`setup.sh` creates a Python virtual environment in `./.venv`, installs `paho-mqtt`
and `aiocoap[all]` into it, and starts Mosquitto via `docker compose`. Ubuntu 24's
system Python is externally-managed (PEP 668), so we install into a venv rather than
fighting it.

**Every new WSL terminal needs `source .venv/bin/activate` before you can run
`python -m src...` commands.**

If you don't want to use `setup.sh`:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
docker compose up -d mosquitto
docker compose ps                      # should show smartfactory-mosquitto running
```

---

## Step-by-step run

### 1. Run the MQTT pipeline (Task 1)

Open **two terminals** (remember `source .venv/bin/activate` in each):

```bash
# Terminal A - subscriber (start this first so it sees retained 'online' messages)
python -m src.mqtt.subscriber
```

```bash
# Terminal B - publisher (every 1 s emits 6 readings at QoS 0/1/2)
python -m src.mqtt.publisher
```

You should see the subscriber print messages in real time and a "Message Summary"
banner every 30 seconds. If you bump any temperature reading above 85 C you'll get
the CRITICAL ALERT banner - to force one, lower the threshold temporarily in
`src/mqtt/subscriber.py` (`CRITICAL_TEMP = 80.0`) and re-run.

Stop both with `Ctrl-C` when you're done.

---

### 2. Run the CoAP pipeline (Task 2)

Again, two terminals:

```bash
# Terminal A - CoAP server (listens on UDP 5683)
python -m src.coap.server
```

```bash
# Terminal B - observer (runs for 60 s, then fetches the Block2 manifest, then exits)
python -m src.coap.observer
```

You'll see Observe notifications arrive every 5 s for both temperature URIs, then a
final summary, then the manifest fetch with byte count and approximate block count.

To exercise the actuator manually you can use `aiocoap-client`:

```bash
# GET the current fan state
aiocoap-client coap://localhost/actuator/line1/fan

# Turn the fan ON  (2.04 Changed expected)
aiocoap-client -m PUT coap://localhost/actuator/line1/fan \
    --content-format 50 --payload '{"state": "ON"}'
```

---

### 3. Run the automated tests

The provided tests live under `tests/`. Make sure Mosquitto is up before running:

```bash
pytest tests/ -v
```

To run only one suite:

```bash
pytest tests/mqtt/test_publisher.py -v
pytest tests/coap/test_server.py    -v
```

### Task 1.3 - QoS comparison experiment

```bash
pytest tests/mqtt/test_qos_loss.py -v -s
```

The `-s` flag is important - it streams the results table to stdout. Copy that
table verbatim into `report/comparison_report.md` (Section 5.1).

On Linux, if you want **real** 10% network loss on loopback (rather than the
in-process drop the harness uses), run before the test:

```bash
sudo tc qdisc add dev lo root netem loss 10%
pytest tests/mqtt/test_qos_loss.py -v -s
sudo tc qdisc del dev lo root   # always undo this
```

---

### 4. Capture packets (Task 4)

Start the publisher and the CoAP server first (each in its own terminal), then in a
third terminal:

```bash
bash scripts/capture.sh
```

This produces:

- `captures/mqtt.pcap` - TCP 1883 traffic
- `captures/coap.pcap` - UDP 5683 traffic

(AMQP is intentionally not captured.)

Inspect with:

```bash
tshark -r captures/mqtt.pcap -V | less
tshark -r captures/coap.pcap -V | less
```

Then fill in `report/packet_analysis.md` with the byte-level annotations.

---

## Project layout

```
smartfactory-iot/
  src/
    mqtt/
      publisher.py      <- Task 1.1
      subscriber.py     <- Task 1.2
    coap/
      server.py         <- Task 2.1
      observer.py       <- Task 2.2
  tests/
    mqtt/
      test_publisher.py
      test_qos_loss.py
    coap/
      test_server.py
  report/
    packet_analysis.md
    comparison_report.md
  captures/                  <- pcap files (git-ignored)
  scripts/
    capture.sh
  config/
    mosquitto.conf
  docker-compose.yml
  requirements.txt
  pytest.ini
  setup.sh
  README.md
```

---

## Troubleshooting

**`ConnectionRefusedError: [Errno 111] Connection refused` from the MQTT scripts**
Mosquitto isn't running. Check `docker compose ps`. If it's not up: `docker compose up -d mosquitto`.

**`ModuleNotFoundError: No module named 'paho'`**
You forgot to activate the venv in this terminal: `source .venv/bin/activate`.

**`error: externally-managed-environment` when running pip directly (Ubuntu 24)**
Don't run `pip install` outside the venv. Activate it first
(`source .venv/bin/activate`) and then pip works normally inside it.

**`docker: Cannot connect to the Docker daemon` in WSL**
Either start Docker Desktop on Windows and enable WSL integration for your distro,
or start the WSL-native daemon: `sudo service docker start`. If you just added
yourself to the `docker` group, close and re-open the WSL terminal so the group
membership takes effect.

**The CoAP observer prints "could not contact server"**
Make sure `python -m src.coap.server` is running in another terminal first, on the
same machine (default `coap://localhost:5683`).

**`tshark: permission denied` or "you don't have permission to capture on that device"**
`sudo dpkg-reconfigure wireshark-common` (answer Yes), `sudo usermod -aG wireshark $USER`,
then close and re-open WSL. As a last resort run `bash scripts/capture.sh` with `sudo`.

**`tc: command not found` when trying to simulate packet loss**
`sudo apt install -y iproute2`. On WSL2 the loopback netem trick may not work in
all kernels - the test harness has an in-process fallback that drops ~10% of
QoS-0 ACKs, so the test still produces meaningful numbers without tc.

**Tests time out**
The pytest config caps each test at 30 s. The QoS loss test publishes ~300 messages
and needs the broker reachable - check Mosquitto logs (`docker compose logs mosquitto`).
