"""
Module 1 Assignment — CoAP Tests (Task 2)
Do not modify this file.
"""
import asyncio
import json
import pytest
import pytest_asyncio

import aiocoap
from aiocoap import Code, Message

# We import lazily to allow skeleton files to exist with NotImplementedError

pytestmark = pytest.mark.asyncio


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="module")
async def coap_server():
    """Start the CoAP server for the duration of the test module."""
    try:
        from src.coap.server import build_server
        ctx = await build_server()
        yield ctx
        await ctx.shutdown()
    except NotImplementedError:
        pytest.skip("CoAP server not yet implemented (NotImplementedError)")


@pytest_asyncio.fixture(scope="module")
async def coap_client():
    ctx = await aiocoap.Context.create_client_context()
    yield ctx
    await ctx.shutdown()


class TestCoAPServer:

    async def test_get_temperature_line1(self, coap_server, coap_client):
        """GET /factory/line1/temperature must return 2.05 with JSON payload."""
        request = Message(code=Code.GET, uri="coap://localhost/factory/line1/temperature")
        response = await coap_client.request(request).response
        assert response.code == Code.CONTENT, \
            f"Expected 2.05 Content, got {response.code}"
        data = json.loads(response.payload)
        assert "value" in data, "Response must contain 'value' key"
        assert "unit" in data, "Response must contain 'unit' key"
        assert data["unit"] == "C", f"Expected unit 'C', got {data['unit']}"

    async def test_get_vibration_line1(self, coap_server, coap_client):
        """GET /factory/line1/vibration must return 2.05 with JSON payload."""
        request = Message(code=Code.GET, uri="coap://localhost/factory/line1/vibration")
        response = await coap_client.request(request).response
        assert response.code == Code.CONTENT
        data = json.loads(response.payload)
        assert "value" in data
        assert data["unit"] == "mm/s"

    async def test_get_power_line1(self, coap_server, coap_client):
        """GET /factory/line1/power must return 2.05 Content."""
        request = Message(code=Code.GET, uri="coap://localhost/factory/line1/power")
        response = await coap_client.request(request).response
        assert response.code == Code.CONTENT
        data = json.loads(response.payload)
        assert data["unit"] == "kW"

    async def test_get_temperature_line2(self, coap_server, coap_client):
        """GET /factory/line2/temperature must work."""
        request = Message(code=Code.GET, uri="coap://localhost/factory/line2/temperature")
        response = await coap_client.request(request).response
        assert response.code == Code.CONTENT

    async def test_put_actuator_on(self, coap_server, coap_client):
        """PUT /actuator/line1/fan with {state:ON} must return 2.04 Changed."""
        payload = json.dumps({"state": "ON"}).encode()
        request = Message(code=Code.PUT,
                          uri="coap://localhost/actuator/line1/fan",
                          payload=payload,
                          content_format=50)
        response = await coap_client.request(request).response
        assert response.code.dotted == "2.04", \
            f"Expected 2.04 Changed, got {response.code}"

        # Verify state change
        get_req = Message(code=Code.GET, uri="coap://localhost/actuator/line1/fan")
        get_resp = await coap_client.request(get_req).response
        data = json.loads(get_resp.payload)
        assert data.get("state") == "ON", f"Fan state should be ON, got {data}"

    async def test_put_actuator_off(self, coap_server, coap_client):
        """PUT /actuator/line1/fan with {state:OFF} must return 2.04 Changed."""
        payload = json.dumps({"state": "OFF"}).encode()
        request = Message(code=Code.PUT,
                          uri="coap://localhost/actuator/line1/fan",
                          payload=payload,
                          content_format=50)
        response = await coap_client.request(request).response
        assert response.code.dotted == "2.04"

    async def test_put_actuator_invalid(self, coap_server, coap_client):
        """PUT /actuator/line1/fan with invalid state must return 4.00 Bad Request."""
        payload = json.dumps({"state": "INVALID"}).encode()
        request = Message(code=Code.PUT,
                          uri="coap://localhost/actuator/line1/fan",
                          payload=payload,
                          content_format=50)
        response = await coap_client.request(request).response
        assert response.code.dotted == "4.00", \
            f"Expected 4.00 Bad Request for invalid state, got {response.code}"

    async def test_block2_manifest_large_enough(self, coap_server, coap_client):
        """GET /factory/manifest must return >= 3072 bytes."""
        request = Message(code=Code.GET, uri="coap://localhost/factory/manifest")
        response = await coap_client.request(request).response
        assert response.code == Code.CONTENT
        assert len(response.payload) >= 3072, \
            f"Manifest too small: {len(response.payload)} bytes (need >= 3072)"

    async def test_block2_manifest_valid_json(self, coap_server, coap_client):
        """GET /factory/manifest payload must be valid JSON."""
        request = Message(code=Code.GET, uri="coap://localhost/factory/manifest")
        response = await coap_client.request(request).response
        try:
            data = json.loads(response.payload)
        except json.JSONDecodeError as e:
            pytest.fail(f"Manifest is not valid JSON: {e}")
        assert isinstance(data, (list, dict)), "Manifest must be a JSON list or object"

    async def test_well_known_core(self, coap_server, coap_client):
        """GET /.well-known/core must return 2.05 Content."""
        request = Message(code=Code.GET, uri="coap://localhost/.well-known/core")
        response = await coap_client.request(request).response
        assert response.code == Code.CONTENT
        payload_str = response.payload.decode()
        assert "/factory" in payload_str or "factory" in payload_str, \
            "/.well-known/core should list factory resources"
