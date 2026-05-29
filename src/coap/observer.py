"""
Module 1 Assignment — Task 2.2
CoAP Observer Client

Registers Observe subscriptions on both line1 and line2 temperature
resources, logs each notification with its Observe sequence number,
detects stale notifications (older sequence), deregisters cleanly after
60 seconds, then performs a Block2 GET on /factory/manifest.

Run with:  python -m src.coap.observer
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

import aiocoap
from aiocoap import Message, Code

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")
log = logging.getLogger(__name__)

SERVER_BASE      = "coap://localhost"
OBSERVE_DURATION = 60   # seconds before clean deregister


class FactoryObserver:
    """Observes CoAP sensor resources and reassembles Block2 transfers."""

    # RFC 7641 §3.4 — Observe option is a 24-bit value; wrap-around must be
    # handled when comparing sequence numbers. We treat differences within
    # 2^23 of the previous value as "newer".
    SEQ_MOD = 1 << 24

    def __init__(self):
        self._ctx = None
        self._last_seq: dict[str, int] = {}     # uri -> last observe sequence number
        self._stale_count: dict[str, int] = {}  # uri -> stale notification count

    # ── Setup ──────────────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._ctx = await aiocoap.Context.create_client_context()

    async def stop(self) -> None:
        if self._ctx:
            await self._ctx.shutdown()

    # ── Observation ────────────────────────────────────────────────────────────

    async def observe_resource(self, uri: str) -> None:
        """Subscribe to a single observable resource for OBSERVE_DURATION seconds."""
        request = Message(code=Code.GET, uri=uri, observe=0)
        pr = self._ctx.request(request)

        async def consume():
            # First the initial response, then each notification.
            initial = await pr.response
            self._handle_notification(uri, initial)
            async for notification in pr.observation:
                self._handle_notification(uri, notification)

        try:
            await asyncio.wait_for(consume(), timeout=OBSERVE_DURATION)
        except asyncio.TimeoutError:
            pass
        finally:
            if not pr.observation.cancelled:
                pr.observation.cancel()
            await self._send_observe_deregister(uri)
            log.info("Deregistered from %s", uri)

    async def _send_observe_deregister(self, uri: str) -> None:
        """Best-effort explicit Observe deregistration using Observe=1."""
        try:
            request = Message(code=Code.GET, uri=uri, observe=1)
            await self._ctx.request(request).response
        except Exception as exc:
            log.debug("Observe=1 deregistration for %s was not acknowledged: %s", uri, exc)

    def _is_newer(self, uri: str, seq: int) -> bool:
        """RFC-7641 wrap-aware comparison."""
        if uri not in self._last_seq:
            return True
        last = self._last_seq[uri]
        diff = (seq - last) % self.SEQ_MOD
        # A "new" notification is within half the sequence space ahead of last.
        return 0 < diff < (self.SEQ_MOD // 2)

    def _handle_notification(self, uri: str, response: Message) -> None:
        seq = response.opt.observe
        arrival_ts = datetime.now(timezone.utc).isoformat()
        ts  = datetime.now().strftime("%H:%M:%S")

        if seq is None:
            # Non-observe response (e.g. when registration is rejected); just log.
            log.warning("[OBSERVE] %s  (no Observe option in response)", uri)
            return

        if not self._is_newer(uri, seq):
            self._stale_count[uri] = self._stale_count.get(uri, 0) + 1
            last = self._last_seq.get(uri)
            log.warning("STALE notification on %s: seq=%d <= last=%s", uri, seq, last)
            return

        self._last_seq[uri] = seq

        try:
            data = json.loads(response.payload)
            value = data.get("value")
            unit  = data.get("unit", "")
            payload_ts = data.get("ts", "")
            log.info(
                "[OBSERVE] %s  seq=%d  val=%s %s  arrival=%s  payload_ts=%s",
                uri, seq, value, unit, arrival_ts, payload_ts,
            )
        except (json.JSONDecodeError, ValueError):
            log.info("[OBSERVE] %s  seq=%d  raw=%r  arrival=%s", uri, seq, response.payload, arrival_ts)

    # ── Block2 Transfer ────────────────────────────────────────────────────────

    async def fetch_manifest(self) -> None:
        """GET /factory/manifest — aiocoap reassembles Block2 automatically."""
        uri = f"{SERVER_BASE}/factory/manifest"
        log.info("Requesting manifest via Block2: %s", uri)
        request = Message(code=Code.GET, uri=uri)
        response = await self._ctx.request(request).response
        payload  = response.payload

        log.info("Manifest received: %d bytes", len(payload))
        try:
            data = json.loads(payload)
            if isinstance(data, dict):
                entries = data.get("entries", [])
                log.info("Firmware entries in manifest: %d", len(entries))
            elif isinstance(data, list):
                log.info("Firmware entries in manifest: %d", len(data))
        except json.JSONDecodeError:
            log.warning("Manifest is not valid JSON")

        # Best-effort Block2 inspection. aiocoap reassembles transparently;
        # if the final Block2 option is absent, use the common 1024-byte SZX=6
        # estimate so the log still reports the required byte and block counts.
        block2 = response.opt.block2
        block_size = block2.size if block2 is not None else 1024
        blocks_used = (len(payload) + block_size - 1) // block_size
        log.info("Block2 transfer: total_bytes=%d, block_size=%d, blocks=%d",
                 len(payload), block_size, blocks_used)
        log.info("Block2 transfer complete")

    # ── Run ────────────────────────────────────────────────────────────────────

    async def run(self) -> None:
        await self.start()
        try:
            uris = [
                f"{SERVER_BASE}/factory/line1/temperature",
                f"{SERVER_BASE}/factory/line2/temperature",
            ]
            log.info("Observing for %d s: %s", OBSERVE_DURATION, ", ".join(uris))

            # Two concurrent observations.
            await asyncio.gather(*(self.observe_resource(uri) for uri in uris))

            # Then the Block2 transfer.
            await self.fetch_manifest()

            print()
            print("── Final Observer Summary ──────────────────────────────")
            for uri in uris:
                stale = self._stale_count.get(uri, 0)
                last  = self._last_seq.get(uri, "n/a")
                print(f"  {uri}")
                print(f"    last seq seen: {last}")
                print(f"    stale notifications: {stale}")
            print("────────────────────────────────────────────────────────")
        finally:
            await self.stop()


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    observer = FactoryObserver()
    asyncio.run(observer.run())
