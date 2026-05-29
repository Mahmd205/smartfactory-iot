"""
Module 1 Assignment — MQTT Tests (Task 1)
Do not modify this file.
"""
import json
import time
import threading
import pytest
import paho.mqtt.client as mqtt
from unittest.mock import patch, MagicMock, call

from src.mqtt.publisher import SmartFactoryPublisher, LINES, SENSORS, CLIENT_ID
from src.mqtt.subscriber import SmartFactorySubscriber, CRITICAL_TEMP


# ─────────────────────────────────────────────────────────────────────────────
# Publisher Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestPublisher:

    def test_connect_uses_persistent_session(self):
        """Publisher must use clean_session=False."""
        with patch('paho.mqtt.client.Client') as MockClient:
            mock_client = MagicMock()
            MockClient.return_value = mock_client
            mock_client.is_connected.return_value = True
            mock_client.on_connect = None

            pub = SmartFactoryPublisher()
            try:
                pub._build_client()
            except NotImplementedError:
                pytest.skip("TODO not yet implemented")

            # Verify clean_session=False was passed to Client()
            call_kwargs = MockClient.call_args
            assert call_kwargs is not None, "mqtt.Client was not instantiated"
            assert (
                call_kwargs.kwargs.get('clean_session') == False or
                (call_kwargs.args and False in call_kwargs.args)
            ), "Publisher must use clean_session=False for persistent session"

    def test_correct_topic_format(self):
        """Publisher topics must follow factory/{line}/{sensor} format."""
        pub = SmartFactoryPublisher()
        try:
            for line in LINES:
                for sensor in SENSORS:
                    topic = pub._topic(line, sensor)
                    assert topic == f"factory/{line}/{sensor}", (
                        f"Expected factory/{line}/{sensor}, got {topic}"
                    )
        except NotImplementedError:
            pytest.skip("TODO not yet implemented")

    def test_publishes_all_six_sensors(self):
        """Publisher must publish readings for all 6 sensors (2 lines × 3 sensors)."""
        pub = SmartFactoryPublisher()
        pub._client = MagicMock()
        published_topics = []

        def fake_publish(topic, payload, qos=0, retain=False):
            published_topics.append(topic)
            result = MagicMock()
            result.rc = 0
            return result

        pub._client.publish = fake_publish

        try:
            for line in LINES:
                for sensor in SENSORS:
                    pub.publish_reading(line, sensor)
        except NotImplementedError:
            pytest.skip("TODO not yet implemented")

        expected = {f"factory/{line}/{sensor}" for line in LINES for sensor in SENSORS}
        assert set(published_topics) == expected, (
            f"Expected topics {expected}, got {set(published_topics)}"
        )

    def test_correct_qos_per_sensor(self):
        """Temperature=QoS1, Vibration=QoS0, Power=QoS2."""
        pub = SmartFactoryPublisher()
        pub._client = MagicMock()
        published_qos = {}

        def fake_publish(topic, payload, qos=0, retain=False):
            published_qos[topic] = qos
            result = MagicMock()
            result.rc = 0
            return result

        pub._client.publish = fake_publish
        expected_qos = {"temperature": 1, "vibration": 0, "power": 2}

        try:
            for line in LINES:
                for sensor in SENSORS:
                    pub.publish_reading(line, sensor)
        except NotImplementedError:
            pytest.skip("TODO not yet implemented")

        for line in LINES:
            for sensor, expected in expected_qos.items():
                topic = f"factory/{line}/{sensor}"
                assert topic in published_qos, f"No message published to {topic}"
                assert published_qos[topic] == expected, (
                    f"{topic}: expected QoS {expected}, got {published_qos[topic]}"
                )

    def test_lwt_configured(self):
        """LWT must be set for factory/line1/status with correct parameters."""
        with patch('paho.mqtt.client.Client') as MockClient:
            mock_instance = MagicMock()
            MockClient.return_value = mock_instance
            pub = SmartFactoryPublisher()
            try:
                pub._build_client()
            except NotImplementedError:
                pytest.skip("TODO not yet implemented")

            will_set_calls = mock_instance.will_set.call_args_list
            assert len(will_set_calls) > 0, "will_set() was never called — LWT not configured"

            # Check line1 LWT
            found = False
            for c in will_set_calls:
                args = c.args if c.args else []
                kwargs = c.kwargs if c.kwargs else {}
                topic   = args[0] if args else kwargs.get('topic', '')
                payload = args[1] if len(args) > 1 else kwargs.get('payload', '')
                retain  = kwargs.get('retain', args[3] if len(args) > 3 else False)
                qos     = kwargs.get('qos', args[2] if len(args) > 2 else 0)

                if 'line1' in topic and 'status' in topic:
                    assert payload == 'offline' or payload == b'offline', \
                        f"LWT payload should be 'offline', got {payload!r}"
                    assert qos == 1, f"LWT QoS should be 1, got {qos}"
                    assert retain is True, "LWT must have retain=True"
                    found = True
                    break

            assert found, "No LWT found for factory/line1/status"

    def test_reading_payload_is_valid_json(self):
        """Published payloads must be valid JSON with value, unit, timestamp, seq fields."""
        pub = SmartFactoryPublisher()
        pub._client = MagicMock()
        payloads = []

        def fake_publish(topic, payload, qos=0, retain=False):
            payloads.append(payload)
            result = MagicMock()
            result.rc = 0
            return result

        pub._client.publish = fake_publish

        try:
            pub.publish_reading("line1", "temperature")
        except NotImplementedError:
            pytest.skip("TODO not yet implemented")

        assert payloads, "No payload was published"
        data = json.loads(payloads[0])
        for field in ["value", "unit", "timestamp", "seq"]:
            assert field in data, f"Payload missing field: {field}"


# ─────────────────────────────────────────────────────────────────────────────
# Subscriber Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestSubscriber:

    def _make_msg(self, topic, payload, qos=1, retain=False):
        msg = MagicMock(spec=mqtt.MQTTMessage)
        msg.topic = topic
        msg.payload = json.dumps(payload).encode() if isinstance(payload, dict) else payload.encode()
        msg.qos = qos
        msg.retain = retain
        return msg

    def test_wildcard_subscription_registered(self):
        """Subscriber must subscribe to factory/# on connect."""
        sub = SmartFactorySubscriber()
        sub._client = MagicMock()
        subscriptions = []

        def fake_subscribe(topic_or_list, qos=0):
            subscriptions.append((topic_or_list, qos))

        sub._client.subscribe = fake_subscribe

        try:
            sub.on_connect(sub._client, None, {}, 0)
        except NotImplementedError:
            pytest.skip("TODO not yet implemented")

        topics_subscribed = [t for t, _ in subscriptions]
        assert "factory/#" in topics_subscribed, (
            f"Expected subscription to 'factory/#', got {topics_subscribed}"
        )

    def test_temperature_qos2_subscription(self):
        """Subscriber must subscribe to factory/+/temperature at QoS 2."""
        sub = SmartFactorySubscriber()
        sub._client = MagicMock()
        subscriptions = []

        def fake_subscribe(topic_or_list, qos=0):
            subscriptions.append((topic_or_list, qos))

        sub._client.subscribe = fake_subscribe
        try:
            sub.on_connect(sub._client, None, {}, 0)
        except NotImplementedError:
            pytest.skip("TODO not yet implemented")

        temp_subs = [(t, q) for t, q in subscriptions if 'temperature' in t]
        assert any(q == 2 for _, q in temp_subs), (
            "Temperature subscription must use QoS 2"
        )

    def test_critical_alert_fires_above_threshold(self, capsys):
        """Critical alert must fire when temperature > 85°C."""
        sub = SmartFactorySubscriber()
        sub._client = MagicMock()

        payload = {"value": 91.5, "unit": "C", "timestamp": "2024-01-01T00:00:00Z"}
        msg = self._make_msg("factory/line1/temperature", payload, qos=1)

        try:
            sub._check_temperature_alert("factory/line1/temperature", payload)
        except NotImplementedError:
            pytest.skip("TODO not yet implemented")

        assert sub._alerts_fired == 1, "Alert counter should be 1 after one critical reading"

    def test_no_alert_below_threshold(self):
        """No alert should fire for normal temperature readings."""
        sub = SmartFactorySubscriber()
        payload = {"value": 72.3, "unit": "C", "timestamp": "2024-01-01T00:00:00Z"}
        try:
            sub._check_temperature_alert("factory/line1/temperature", payload)
        except NotImplementedError:
            pytest.skip("TODO not yet implemented")
        assert sub._alerts_fired == 0, "No alert should fire for temperature below threshold"

    def test_message_counts_tracked(self):
        """Message counter must increment per topic."""
        sub = SmartFactorySubscriber()
        sub._client = MagicMock()
        msg = self._make_msg("factory/line1/temperature",
                             {"value": 70.0, "unit": "C", "timestamp": "2024-01-01T00:00:00Z"})
        try:
            sub.on_message(sub._client, None, msg)
            sub.on_message(sub._client, None, msg)
        except NotImplementedError:
            pytest.skip("TODO not yet implemented")

        assert sub._msg_counts["factory/line1/temperature"] == 2
