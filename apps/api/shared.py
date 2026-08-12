"""Cross-instance counters, backed by Redis when one is configured.

Why only counters live here. The provider blocks CONFIG GET, so its eviction
policy cannot be verified — and an evicting store is the wrong home for anything
whose loss changes a security answer. A dropped rate-limit counter costs someone a
few extra requests; a dropped replay record makes a captured request valid again.
So nonces stay in Postgres behind a unique constraint (see platformapi.UsedNonce)
and only the limiters come here.

With no PHOTOBIND_REDIS_URL the limiter falls back to per-process memory, which is
correct on one instance — the same behaviour as before, so a Redis outage degrades
the ceiling rather than opening it or taking the service down.
"""
from __future__ import annotations

import logging
import os
import time

log = logging.getLogger("photobind.shared")

_client = None
_checked = False


def client():
    """The Redis handle, or None. Connected once, lazily, and never retried in a
    hot path — a limiter that blocks on a dead cache is worse than no limiter."""
    global _client, _checked
    if _checked:
        return _client
    _checked = True
    url = os.environ.get("PHOTOBIND_REDIS_URL", "").strip()
    if not url:
        return None
    try:
        import redis
        c = redis.from_url(url, socket_timeout=1.5, socket_connect_timeout=1.5,
                           health_check_interval=30, decode_responses=True)
        c.ping()
        _client = c
        log.info("shared counters: redis connected")
    except Exception as e:
        log.warning("shared counters: redis unavailable (%s); using per-process "
                    "counters, so keep max instances at 1", type(e).__name__)
        _client = None
    return _client


_local: dict[str, list[float]] = {}


def hits_in_window(bucket: str, window_s: int) -> int:
    """Count this hit and return how many have happened in the window.

    Redis path is a fixed window: INCR a key named for the period, expire it, done.
    One round trip, no sorted sets to trim, and the worst case of a fixed window —
    a caller straddling the boundary — is a brief 2x, which is an acceptable price
    for a limiter that costs one command.
    """
    r = client()
    if r is not None:
        period = int(time.time()) // window_s
        key = f"pb:rl:{bucket}:{period}"
        try:
            pipe = r.pipeline()
            pipe.incr(key)
            pipe.expire(key, window_s + 5)
            return int(pipe.execute()[0])
        except Exception:
            # Fall through to memory rather than failing the request: a cache
            # blip must not become an outage.
            pass
    now = time.time()
    seen = [t for t in _local.get(bucket, []) if now - t < window_s]
    seen.append(now)
    _local[bucket] = seen
    if len(_local) > 20000:                  # crude ceiling; process-local only
        for k in list(_local)[:5000]:
            _local.pop(k, None)
    return len(seen)


def reset_local() -> None:
    """Drop the per-process fallback counters.

    Called when an app is constructed. Without it the fallback outlives the app
    that used it, which is invisible in production (one app per process) and wrong
    anywhere a second app is built in the same interpreter — a test suite, or a
    future worker sharing the module.
    """
    _local.clear()


def backend() -> str:
    return "redis" if client() is not None else "in-process"
