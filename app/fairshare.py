"""Anonymous visitor sessions, and a fair share of lookups for each of them.

Photos stay on the phone, so a session holds nothing private: it's a random id in a cookie,
used to keep one visitor (or a script) from slowing down everyone else at the demo.

- One lookup at a time per session. A second one waits its turn instead of failing.
- At most LOOKUPS_PER_SESSION a minute per session; past that, 429 with Retry-After.
- At most LOOKUPS_PER_IP a minute per IP, a loose backstop for clients that drop the cookie.
  Venue Wi-Fi puts most of the room behind one IP, so this stays high.
- At most POD_SLOTS lookups at once per pod, so /api/slide and /api/talk always get a
  thread from the pool they share with lookups. A lookup that waits WAIT_SEC for its turn
  gets 503, and the page keeps the photo queued.

The counts live in this process. With two pods and no session affinity, a visitor gets up
to twice the limits, which is fine for a day.
"""
import asyncio
import re
import secrets
import time
from collections import deque
from contextlib import asynccontextmanager

COOKIE = "sk_session"
COOKIE_MAX_AGE = 7 * 86400
SESSION_ID = re.compile(r"[A-Za-z0-9_-]{22}")  # secrets.token_urlsafe(16)

WINDOW_SEC = 60
LOOKUPS_PER_SESSION = 20
LOOKUPS_PER_IP = 300
POD_SLOTS = 4
WAIT_SEC = 15.0


class Limited(Exception):
    def __init__(self, retry_after):
        self.retry_after = max(1, int(retry_after + 0.999))


class Busy(Exception):
    pass


def session_id(cookie):
    """The visitor's id from their cookie, and whether it's new and needs setting."""
    if cookie and SESSION_ID.fullmatch(cookie):
        return cookie, False
    return secrets.token_urlsafe(16), True


def client_ip(request):
    # The Google load balancer appends "<client>, <load balancer>" to X-Forwarded-For.
    # Anything before those came from the client, so it can't be trusted.
    hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
    if len(hops) >= 2:
        return hops[-2]
    return request.client.host if request.client else "unknown"


_hits = {}  # "s:<session>" or "ip:<address>" -> deque of lookup times
_last_sweep = 0.0


def _wait(key, limit, now):
    """Seconds until `key` may look up again, or 0 if it may now."""
    q = _hits.get(key)
    if not q:
        return 0
    while q and q[0] <= now - WINDOW_SEC:
        q.popleft()
    return q[0] + WINDOW_SEC - now if len(q) >= limit else 0


def admit(session, ip):
    """Count a lookup, or raise Limited if the session or the IP is over its limit."""
    global _last_sweep
    now = time.monotonic()
    if now - _last_sweep > WINDOW_SEC:  # forget visitors who've gone quiet
        _last_sweep = now
        for key in [k for k, q in _hits.items() if not q or q[-1] <= now - WINDOW_SEC]:
            del _hits[key]
    keys = (f"s:{session}", LOOKUPS_PER_SESSION), (f"ip:{ip}", LOOKUPS_PER_IP)
    wait = max(_wait(key, limit, now) for key, limit in keys)
    if wait:
        raise Limited(wait)
    for key, _ in keys:
        _hits.setdefault(key, deque()).append(now)


_turns = {}  # session -> [its lock, requests holding or waiting for it]
_slots = asyncio.Semaphore(POD_SLOTS)


@asynccontextmanager
async def turn(session):
    """Wait for this session's turn and a free slot on the pod, or raise Busy."""
    entry = _turns.setdefault(session, [asyncio.Lock(), 0])
    entry[1] += 1
    lock = entry[0]
    try:
        try:
            async with asyncio.timeout(WAIT_SEC):
                await lock.acquire()
                try:
                    await _slots.acquire()
                except BaseException:
                    lock.release()
                    raise
        except TimeoutError:
            raise Busy from None
        try:
            yield
        finally:
            _slots.release()
            lock.release()
    finally:
        entry[1] -= 1
        if not entry[1]:
            del _turns[session]
