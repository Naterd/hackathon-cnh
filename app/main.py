import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

HERE = Path(__file__).parent

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
