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

import search

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
    photo = bytearray()
    async for chunk in request.stream():
        photo += chunk
        if len(photo) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Photo too large")
    if not photo:
        raise HTTPException(400, "Send the photo as the request body")

    try:
        return await call(search.lookup, bytes(photo))
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
