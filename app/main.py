import logging
import os
import re
import threading
import urllib.error
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import fairshare
import search

# The Dash0 operator injects the OpenTelemetry SDK in the pod. Without it (locally), no spans.
try:
    from opentelemetry import trace
except ImportError:
    trace = None

HERE = Path(__file__).parent

# The browser uploads a 1280 px JPEG of about 300 KB. The cap only stops a huge upload
# from filling the pod's memory.
MAX_UPLOAD_BYTES = 10_000_000

log = logging.getLogger("slidekick")


@asynccontextmanager
async def lifespan(app):
    # In the background, so a cold embedding service doesn't hold the pod out of rotation.
    if search.configured():
        threading.Thread(target=search.warm_up, daemon=True).start()
    yield


app = FastAPI(openapi_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

templates = Jinja2Templates(directory=HERE / "templates")
templates.env.globals["version"] = os.environ.get("APP_VERSION", "dev")
# Ingest-only Dash0 token for browser monitoring, from Secret app-credentials. It ends up in the page.
templates.env.globals["dash0_web_token"] = os.environ.get("DASH0_WEB_TOKEN")

# Set only on these, not on /static or /api/slide, which a shared cache may keep.
SESSION_PATHS = {"/", "/api/lookup"}


@app.middleware("http")
async def session(request: Request, call_next):
    request.state.session, new = fairshare.session_id(request.cookies.get(fairshare.COOKIE))
    if trace:
        # So Dash0 can show one visitor's requests. The id is random and holds nothing personal.
        trace.get_current_span().set_attribute("slidekick.session", request.state.session)
    response = await call_next(request)
    if new and request.url.path in SESSION_PATHS:
        response.set_cookie(
            fairshare.COOKIE, request.state.session, max_age=fairshare.COOKIE_MAX_AGE,
            httponly=True, secure=True, samesite="lax",
        )
    return response


@app.get("/healthz", response_class=PlainTextResponse)
@app.get("/readyz", response_class=PlainTextResponse)
def health():
    # The load balancer checks /readyz. Keep Elasticsearch out of it, or a slow
    # backend takes every pod out of rotation.
    return "ok\n"


@app.get("/")
def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


# Served from the root, not /static, so the worker's scope covers the whole app.
@app.get("/sw.js")
def service_worker(request: Request):
    return templates.TemplateResponse(
        request, "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"}
    )


@app.post("/api/lookup")
async def lookup(request: Request):
    try:
        fairshare.admit(request.state.session, fairshare.client_ip(request))
    except fairshare.Limited as e:
        log.warning("Lookup limited: too many from one visitor")
        raise HTTPException(429, "Too many photos at once", headers={"Retry-After": str(e.retry_after)})

    photo = bytearray()
    async for chunk in request.stream():
        photo += chunk
        if len(photo) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Photo too large")
    if not photo:
        raise HTTPException(400, "Send the photo as the request body")

    try:
        async with fairshare.turn(request.state.session):
            return await call(search.lookup, bytes(photo))
    except fairshare.Busy:
        log.warning("Lookup busy: no free slot in %ss", fairshare.WAIT_SEC)
        raise HTTPException(503, "Search is busy. Try again in a moment.")
    except search.BadPhoto:
        raise HTTPException(400, "That isn't an image we can read")


# The ids go into Elasticsearch URLs, so only YouTube video ids and still ids get through.
TALK_ID = re.compile(r"[A-Za-z0-9_-]{11}")
STILL_ID = re.compile(r"[A-Za-z0-9_-]{11}-\d{3}")


@app.get("/api/slide/{still_id}")
async def slide(still_id: str):
    if not STILL_ID.fullmatch(still_id):
        raise HTTPException(404, "No such slide")
    media_type, image = await call(search.slide, still_id, missing="No such slide")
    return Response(image, media_type=media_type, headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/talk/{talk_id}")
async def talk(talk_id: str):
    found = TALK_ID.fullmatch(talk_id) and await call(search.talk, talk_id)
    if not found:
        raise HTTPException(404, "No such talk")
    return found


# `missing` is for single-document reads, where an Elasticsearch 404 means no such id. On a search
# it means the index or alias is gone, a server problem: the page keeps a photo for any 5xx but
# drops one on a 4xx.
async def call(fn, *args, missing=None):
    if not search.configured():
        raise HTTPException(503, "Search isn't configured: set ELASTIC_URL and ELASTIC_ES_API_KEY")
    try:
        return await run_in_threadpool(fn, *args)
    except urllib.error.HTTPError as e:
        if missing and e.code == 404:
            raise HTTPException(404, missing)
        # The status only: the response body can echo the query, which holds the photo.
        log.warning("Elasticsearch returned HTTP %s", e.code)
        raise HTTPException(502, "Search failed")
    except (urllib.error.URLError, TimeoutError) as e:
        log.warning("Elasticsearch unreachable: %s", type(e).__name__)
        raise HTTPException(502, "Search failed")
