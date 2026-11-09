import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

HERE = Path(__file__).parent

# The browser uploads a 1280 px JPEG of about 300 KB. The cap only stops a huge upload
# from filling the pod's memory.
MAX_UPLOAD_BYTES = 10_000_000

app = FastAPI(openapi_url=None)
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

    # Stub until the Elasticsearch lookup lands: every photo matches the talk behind
    # docs/test-photos/appset-603s.jpg. Talks are ranked best first, like lookup() in
    # docs/search_example.py, with the moments where the slide appeared.
    return {
        "talks": [
            {
                "talk_id": "9RvDczVIiS0",
                "title": "Mastering ApplicationSet: Advanced Argo CD Automation",
                "speakers": "Alexander Matyushentsev, Akuity",
                "moments": [
                    {"id": "9RvDczVIiS0-008", "start_sec": 549.5, "url": "https://youtu.be/9RvDczVIiS0?t=549"}
                ],
            }
        ]
    }
