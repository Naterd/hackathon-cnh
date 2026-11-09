"""Visitor sessions and the fair share of lookups, with a fake search. Run from app/:
python ../tests/fairshare_test.py"""

import asyncio
import sys
import threading
import time

import httpx

sys.path.insert(0, ".")
import fairshare  # noqa: E402
import search  # noqa: E402
from main import app  # noqa: E402

running = {}  # session -> lookups running now
most = {}  # session -> the most that ran at once
guard = threading.Lock()


def fake_lookup(photo):
    who = photo.decode()
    with guard:
        running[who] = running.get(who, 0) + 1
        most[who] = max(most.get(who, 0), running[who])
    time.sleep(0.2)
    with guard:
        running[who] -= 1
    return {"talks": []}


search.configured = lambda: True
search.lookup = fake_lookup

failed = 0


def check(name, ok, detail=""):
    global failed
    failed += not ok
    print(f"{'ok  ' if ok else 'FAIL'} {name}{': ' + detail if detail and not ok else ''}")


def visitor():
    # https, or the client won't send the Secure cookie back.
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://slidekick.test")


def reset():
    fairshare._hits.clear()
    running.clear()
    most.clear()


async def main():
    async with visitor() as a, visitor() as b:
        r = await a.get("/")
        check("/ sets the session cookie", fairshare.COOKIE in r.cookies)
        sid = a.cookies[fairshare.COOKIE]
        await a.get("/")
        check("the cookie is kept", a.cookies[fairshare.COOKIE] == sid)
        r = await a.get("/static/style.css")
        check("/static doesn't set a cookie", "set-cookie" not in r.headers)
        await b.get("/")
        check("two visitors get two sessions", b.cookies[fairshare.COOKIE] != sid)

        reset()
        rs = await asyncio.gather(*(a.post("/api/lookup", content=b"a") for _ in range(3)))
        check("three at once from one visitor all match", [r.status_code for r in rs] == [200] * 3,
              str([r.status_code for r in rs]))
        check("...one at a time", most.get("a") == 1, str(most))

        reset()
        codes = [(await a.post("/api/lookup", content=b"a")).status_code
                 for _ in range(fairshare.LOOKUPS_PER_SESSION)]
        check("a visitor gets their full minute", codes == [200] * len(codes), str(codes))
        r = await a.post("/api/lookup", content=b"a")
        check("one more is 429", r.status_code == 429, str(r.status_code))
        check("...with Retry-After", int(r.headers.get("retry-after", 0)) >= 1, str(r.headers))
        r = await b.post("/api/lookup", content=b"b")
        check("another visitor still matches", r.status_code == 200, str(r.status_code))

        reset()
        slots, wait = fairshare._slots, fairshare.WAIT_SEC
        fairshare._slots, fairshare.WAIT_SEC = asyncio.Semaphore(1), 0.05
        try:
            rs = await asyncio.gather(a.post("/api/lookup", content=b"a"), b.post("/api/lookup", content=b"b"))
        finally:
            fairshare._slots, fairshare.WAIT_SEC = slots, wait
        codes = sorted(r.status_code for r in rs)
        check("a full pod answers 503", codes == [200, 503], str(codes))
        check("no sessions left waiting", not fairshare._turns, str(fairshare._turns))

    reset()
    class Fake:  # just what client_ip reads
        def __init__(self, xff):
            self.headers = {"x-forwarded-for": xff}
            self.client = None
    check("client IP is the hop before the load balancer",
          fairshare.client_ip(Fake("1.2.3.4, 5.6.7.8, 34.1.1.1")) == "5.6.7.8")


asyncio.run(main())
sys.exit(1 if failed else 0)
