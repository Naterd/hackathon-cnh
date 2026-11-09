import logging
import os
import threading
import urllib.error
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse
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
    if not search.configured():
        raise HTTPException(503, "Search isn't configured: set ELASTIC_URL and ELASTIC_ES_API_KEY")

    try:
        return await run_in_threadpool(search.lookup, bytes(photo))
    except search.BadPhoto:
        raise HTTPException(400, "That isn't an image we can read")
    except urllib.error.HTTPError as e:
        # The status only: the response body can echo the query, which holds the photo.
        log.warning("Elasticsearch returned HTTP %s", e.code)
        raise HTTPException(502, "Search failed")
    except (urllib.error.URLError, TimeoutError) as e:
        log.warning("Elasticsearch unreachable: %s", type(e).__name__)
        raise HTTPException(502, "Search failed")
