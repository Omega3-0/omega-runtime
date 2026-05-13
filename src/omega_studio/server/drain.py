"""In-flight request tracking + graceful shutdown drain.

Without this, uvicorn's default SIGINT/SIGTERM handling cancels every
in-flight request at the ASGI receive channel — clients see truncated
streams, half-written embeddings, and broken transactions mid-operation.
The drain pattern instead:

1. ``InFlightTracker`` counts active requests (incremented in middleware,
   decremented in a try/finally so cancellation still releases the slot).
2. On shutdown the lifespan poller waits ``OMEGA_DRAIN_TIMEOUT_S`` (default
   30s) for the counter to reach 0 before yielding to uvicorn's hard stop.
3. Once draining starts, new requests against ``/v1/*`` are rejected with
   503 ``service_draining`` so load balancers can drain too.

Health (``/health``) is NOT 503'd during drain — that's how operators see
the server is shutting down, and it's how monitoring detects the state.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

log = logging.getLogger("omega_studio.server.drain")


def _drain_timeout_s() -> float:
    raw = os.environ.get("OMEGA_DRAIN_TIMEOUT_S", "").strip()
    if not raw:
        return 30.0
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 30.0


class InFlightTracker:
    """Async-safe counter for active requests + a shutting-down flag.

    Uses an ``asyncio.Lock`` only for the counter mutation so the path
    is contention-free under normal load (each request touches the lock
    twice — enter + exit). The shutting-down flag is read-mostly and
    written once during shutdown; bare attribute access is fine.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._count = 0
        self.shutting_down = False
        self._drained = asyncio.Event()
        self._drained.set()  # starts drained (count == 0)

    @property
    def count(self) -> int:
        return self._count

    async def acquire(self) -> None:
        """Mark a request as in-flight. Call from middleware on entry."""
        async with self._lock:
            self._count += 1
            self._drained.clear()

    async def release(self) -> None:
        """Mark a request as completed. Call from middleware finally."""
        async with self._lock:
            self._count = max(0, self._count - 1)
            if self._count == 0:
                self._drained.set()

    async def wait_for_drain(self, timeout_s: float | None = None) -> bool:
        """Block until count reaches 0 or the timeout elapses.

        Returns True if drained cleanly, False on timeout. Callers
        (lifespan shutdown) use the bool to decide whether to log a
        forceful-shutdown warning before yielding to uvicorn.
        """
        if self._count == 0:
            return True
        try:
            await asyncio.wait_for(
                self._drained.wait(),
                timeout=timeout_s if timeout_s is not None else _drain_timeout_s(),
            )
            return True
        except asyncio.TimeoutError:
            return False


async def drain_on_shutdown(tracker: InFlightTracker) -> None:
    """Called from the FastAPI lifespan shutdown branch.

    Flips ``shutting_down`` so new ``/v1/*`` requests get 503, then
    waits up to ``OMEGA_DRAIN_TIMEOUT_S`` for in-flight requests to
    finish. Emits a single info line on success or a warning if the
    timeout fires (the latter is operator-visible — they wanted to
    know the server didn't drain cleanly).
    """
    tracker.shutting_down = True
    start = time.monotonic()
    initial = tracker.count
    if initial == 0:
        log.info("shutdown: no in-flight requests, draining instantly")
        return
    log.info("shutdown: draining %d in-flight request(s)...", initial)
    drained = await tracker.wait_for_drain()
    elapsed = time.monotonic() - start
    if drained:
        log.info(
            "shutdown: drained %d request(s) in %.2fs", initial, elapsed,
        )
    else:
        log.warning(
            "shutdown: drain timeout after %.2fs; %d request(s) still in-flight, "
            "forcing exit",
            elapsed,
            tracker.count,
        )
