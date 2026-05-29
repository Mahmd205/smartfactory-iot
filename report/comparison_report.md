# Module 1 Assignment — Protocol Comparison Report

**Student Name:** ___________________________
**Student ID:**   ___________________________
**Date:**         ___________________________

---

## 5.1 QoS Comparison Results Table

**Measurement note:** Self-contained Python measurement; MQTT duration=60s; messages_per_qos=100; CoAP requests per mode=50; duplicates counted as repeated MQTT sequence numbers and repeated CoAP response Message IDs; netem disabled.

| Protocol / QoS | Sent | Received | Lost (%) | Duplicates | Avg Latency (ms) |
|----------------|------|----------|----------|------------|------------------|
| MQTT QoS 0 | 100 | 100 | 0.0% | 0 | 181.2 |
| MQTT QoS 1 | 100 | 100 | 0.0% | 0 | 218.8 |
| MQTT QoS 2 | 100 | 100 | 0.0% | 0 | 249.8 |
| CoAP NON | 50 | 36 | 28.0% | 0 | 1.9 |
| CoAP CON | 50 | 46 | 8.0% | 0 | 46.2 |
| AMQP (skipped) | - | - | - | - | - |
## 5.2 CoAP–HTTP Proxy Mapping

> Run `pytest tests/coap/test_proxy.py -v -s` and record the observed HTTP headers.

| HTTP Header | CoAP Option | Your Observed Value |
|-------------|-------------|---------------------|
| Content-Type | | |
| Cache-Control: max-age | | |
| ETag | | |
| Location | | |

---

## 5.3 Protocol Selection Recommendation

### Data Path Recommendations

| Data Path | Recommended Protocol | Justification |
|-----------|----------------------|---------------|
| Sensor → Cloud, high frequency, <100 ms latency | MQTT QoS 0 or QoS 1 | MQTT gives a lightweight publish/subscribe model, topic hierarchy, persistent sessions, retained status messages, and broker decoupling. QoS 0 is lowest latency for non-critical high-rate telemetry; QoS 1 is safer when loss is unacceptable. |
| Actuator commands, safety-critical, exactly-once | MQTT QoS 2 for this project scope; CoAP CON only if request/response control is required | QoS 2 gives MQTT-level exactly-once processing between client and broker. CoAP Confirmable messages acknowledge delivery but do not provide exactly-once command semantics by themselves, so idempotency or command IDs would still be needed. |
| Backend service-to-service routing | AMQP in a full system; MQTT if AMQP is excluded | AMQP is normally strongest for backend routing because exchanges, queues, acknowledgements, dead-lettering, and consumer prefetch are designed for service-to-service workflows. Since AMQP is ignored in this submission, MQTT topic routing is the available alternative. |
| OTA firmware delivery to constrained MCU, Class 2 | CoAP Block2 | CoAP is designed for constrained devices over UDP and supports block-wise transfer. The `/factory/manifest` resource demonstrates a large JSON object that can be transferred as blocks and reassembled by the client. |

### Detailed Justification

For SmartFactory telemetry, MQTT is the best fit for the Sensor → Cloud path. The implementation publishes six independent data streams using the topic hierarchy `factory/{line}/{sensor_type}`, which allows a subscriber to consume either all telemetry through `factory/#` or a narrower class of events such as `factory/+/temperature`. This topic structure is valuable in a factory system because routing policy can be expressed directly in the topic namespace. MQTT also supports retained status messages, which the publisher uses for line status, and a Last Will and Testament, which allows the broker to mark a line offline when the publisher disconnects unexpectedly. For high-frequency telemetry with a tight latency target, QoS 0 gives minimum protocol overhead, while QoS 1 is the practical choice when occasional packet loss is less acceptable. The final decision should be based on the measured loss and latency values from Section 5.1.

For safety-critical actuator commands, the preferred option in this project scope is MQTT QoS 2 because it provides exactly-once MQTT delivery semantics. This matters for commands such as turning a cooling fan ON or OFF because duplicate command processing can hide logic errors or create inconsistent state. However, exactly-once delivery at the MQTT protocol layer does not replace application-level safety design. A real deployment should still include command identifiers, idempotent actuator logic, acknowledgement from the device, timeout handling, and authorization. CoAP Confirmable messages are also useful for direct request/response control because the client receives a clear response code such as `2.04 Changed`, as implemented by the fan resource. However, a CoAP CON exchange only confirms receipt of a message; it does not by itself provide the stronger exactly-once processing behavior of MQTT QoS 2.

For backend service-to-service routing, AMQP would normally be the strongest protocol because it supports exchange-based routing, durable queues, manual acknowledgements, publisher confirms, prefetch control, and dead-letter exchanges. These features are directly aligned with backend processing pipelines where failed messages need to be retried, parked, or inspected. Since the assignment version used here marks AMQP as ignored, the implemented alternative is MQTT topic-based routing. MQTT is simpler and sufficient for basic telemetry distribution, but it is less expressive than AMQP for complex backend workflows such as dead-letter inspection or work-queue load balancing.

For OTA firmware delivery to constrained microcontrollers, CoAP with Block2 transfer is the best choice. CoAP has compact binary headers and is designed for constrained nodes and constrained networks. The project’s `/factory/manifest` resource returns a JSON manifest larger than 3 KB, which forces block-wise transfer behavior. This is a better model for firmware metadata delivery than MQTT topics because the MCU can request a specific resource, cache it, and reassemble blocks under application control. The packet capture should show CoAP option fields such as Uri-Path, Content-Format, and Observe/Block-related options, making CoAP easier to map to resource-oriented device management.

---

## 5.4 Reflection

### Technical Challenge

One technical challenge in the implementation was making the CoAP sensor resources observable while still keeping the server simple. A normal CoAP GET resource can return the current value once, but an observable resource must update internal state and notify registered clients whenever a new reading is available. This was handled by implementing sensor resources as `ObservableResource` objects and scheduling an `asyncio` update loop that refreshes the simulated reading every five seconds. After each update, the server calls `updated_state()` so active Observe clients receive a notification without polling. This design also required careful asynchronous startup, because the update task must be created while an event loop is running.

### Most Surprising Protocol Difference

The most surprising difference was how visible the protocol design goals become in packet capture. MQTT traffic is built around broker-mediated message exchange and QoS handshakes. A QoS 1 publish is not just a data packet; it is paired with a PUBACK that carries the same packet identifier. CoAP looks much more like a compact REST protocol: the request identifies a resource path through Uri-Path options, and the response uses a small code such as `2.05 Content` plus a payload marker before the JSON body. This makes CoAP packet annotation feel closer to HTTP semantics, while MQTT packet annotation feels closer to message-queue delivery semantics.

### Most Complex Protocol to Implement

CoAP was the most complex part of the implemented MQTT/CoAP scope because the server had to support multiple interaction models: simple GET, observable resources, PUT-based actuator control, and large manifest transfer. MQTT required careful QoS and topic configuration, but once the publisher and subscriber were connected to the broker, the broker handled most routing behavior. In contrast, the CoAP server had to manage resources, JSON parsing, response codes, observer notifications, and asynchronous updates inside the application code. The observer client also had to track Observe sequence numbers and detect stale notifications, which added state-management complexity beyond a simple request/response client.
---

*Module 1 Assignment — Real-Time Data Analytics for IoT*
