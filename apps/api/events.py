"""Instant updates: a per-user event stream so every open web tab and every
phone reflects a change the moment it happens, with no refresh.

Transport is Server-Sent Events. Chosen over WebSockets deliberately: it is
one plain HTTP GET, so it works through Cloudflare and Cloud Run with no
protocol upgrade, reconnects on its own, and needs no client library.

Delivery is at-most-once and in-memory: a subscriber that is not connected at
the moment of an event does not receive it later. That is the honest scope —
the client treats an event as "something changed, re-read it", so a missed
event costs a stale view until the next event or reconnect, never a wrong
write. A durable bus (Redis/Pub-Sub) would be required for multi-instance
fan-out; with more than one Cloud Run instance, a client only sees events
raised by the instance it is attached to. Documented, not hidden.
"""

import asyncio
import json
import logging
from collections import defaultdict

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

log = logging.getLogger("photobind.events")

# How long to wait before sending a keep-alive comment. Cloudflare and Cloud
# Run both drop idle streams; a comment line costs nothing and keeps the
# connection (and therefore the "instant" part) alive.
KEEPALIVE_S = 20
QUEUE_MAX = 50          # per subscriber; slow readers get dropped, not buffered


class EventBus:
    """Fan-out to the connected subscribers of one user."""

    def __init__(self):
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, user_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        self._subs[user_id].add(q)
        return q

    def unsubscribe(self, user_id: str, q: asyncio.Queue) -> None:
        self._subs[user_id].discard(q)
        if not self._subs[user_id]:
            self._subs.pop(user_id, None)

    def publish(self, user_id: str, event: str, data: dict | None = None) -> int:
        """Non-blocking. A full queue means that subscriber is not keeping up;
        dropping is correct because every event only says 'refetch'."""
        payload = json.dumps({"event": event, **(data or {})})
        delivered = 0
        for q in list(self._subs.get(user_id, ())):
            try:
                q.put_nowait(payload)
                delivered += 1
            except asyncio.QueueFull:
                log.warning("dropping event for a slow subscriber (%s)", event)
        return delivered

    def subscriber_count(self, user_id: str) -> int:
        return len(self._subs.get(user_id, ()))


bus = EventBus()

# Events raised for everyone, not one account (a new app release, for example).
BROADCAST = "*"


def make_router(resolve_user) -> APIRouter:
    """`resolve_user(request, db)` returns the signed-in User or None."""
    router = APIRouter()

    @router.get("/v1/events")
    async def events(request: Request):
        user = resolve_user(request)
        # Signed-out clients still get broadcasts (release published), so the
        # download button can update itself without a reload.
        key = user.user_id if user else BROADCAST
        own = bus.subscribe(key)
        broadcast = bus.subscribe(BROADCAST) if user else None

        async def stream():
            # Tell the client the stream is alive before anything happens, so
            # it can distinguish "connected, quiet" from "never connected".
            yield "event: ready\ndata: {}\n\n"
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    getters = [asyncio.create_task(own.get())]
                    if broadcast is not None:
                        getters.append(asyncio.create_task(broadcast.get()))
                    done, pending = await asyncio.wait(
                        getters, timeout=KEEPALIVE_S,
                        return_when=asyncio.FIRST_COMPLETED)
                    for t in pending:
                        t.cancel()
                    if not done:
                        yield ": keepalive\n\n"      # comment: ignored by clients
                        continue
                    for t in done:
                        yield f"data: {t.result()}\n\n"
            except asyncio.CancelledError:           # client went away
                raise
            finally:
                bus.unsubscribe(key, own)
                if broadcast is not None:
                    bus.unsubscribe(BROADCAST, broadcast)

        return StreamingResponse(
            stream(), media_type="text/event-stream",
            headers={
                "Cache-Control": "no-store",
                # Stops any intermediary from buffering the stream, which
                # would defeat the whole point.
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            })

    return router
