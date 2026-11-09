"""Phone photo in, talks and YouTube moments out. docs/search.md explains the index and
the query. The lookup is docs/search_example.py's, which ingest/eval.py measures."""
import base64
import functools
import io
import json
import logging
import os
import sys
import contextvars
import threading
import time
import urllib.request
from contextlib import nullcontext
from pathlib import Path

import numpy as np
from PIL import Image
from rapidocr_onnxruntime import RapidOCR

try:  # The API only; the Dash0 operator injects the SDK. Without it every call is a no-op.
    from opentelemetry import trace
except ImportError:
    trace = None

try:  # the image ships it next to this file
    import search_example
except ImportError:  # a checkout keeps it in docs/
    sys.path.append(str(Path(__file__).resolve().parent.parent / "docs"))
    import search_example

INDEX = "kubecon-moments"  # alias; points at the current index version
MAX_PIXELS = 25_000_000  # the page sends at most 2560 px a side, about 5M pixels

log = logging.getLogger("slidekick.search")

# Loading the models takes about a second, so do it once. Lookups run in a thread pool,
# and the engine isn't documented as thread-safe, so one OCR at a time (about 0.5 s).
ocr = RapidOCR()
ocr_lock = threading.Lock()


# search_example makes an engine per thread, about 300 MB each. The thread pool also serves
# /api/slide and grows to dozens of threads, which would take the pod past its 2Gi limit.
def one_ocr(img):
    with _timed("ocr"), ocr_lock:
        result, _ = ocr(img)
    return [(text, *np.mean(box, axis=0)) for box, text, _ in (result or [])]


# Time and trace search_example's OCR and Elasticsearch calls. It makes them on the calling
# thread, through these module names, so the request's trace and _timings reach them.
_tracer = trace.get_tracer("slidekick.lookup") if trace else None
_timings = contextvars.ContextVar("lookup_timings", default=None)


class _timed:
    def __init__(self, phase):
        self.phase = phase
        self.span = _tracer.start_as_current_span(f"lookup.{phase}") if _tracer else nullcontext()

    def __enter__(self):
        self.span.__enter__()
        self.start = time.perf_counter()

    def __exit__(self, *exc):
        t = _timings.get()
        if t is not None:
            t[self.phase] = t.get(self.phase, 0.0) + time.perf_counter() - self.start
        return self.span.__exit__(*exc)


_reference_es = search_example.es


def _traced_es(*args, **kwargs):
    with _timed("search"):
        return _reference_es(*args, **kwargs)


search_example.ocr = one_ocr
search_example.es = _traced_es
Throttled = search_example.Throttled


def configured():
    return bool(os.environ.get("ELASTIC_URL") and os.environ.get("ELASTIC_ES_API_KEY"))


def es(method, path, body=None):
    req = urllib.request.Request(
        os.environ["ELASTIC_URL"].rstrip("/") + path,
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={
            "Authorization": "ApiKey " + os.environ["ELASTIC_ES_API_KEY"],
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


class BadPhoto(ValueError):
    """The upload isn't an image Pillow can read, or has too many pixels to decode safely."""


def lookup(photo):
    # search_example decodes the whole photo at full size with no pixel cap, so check it here first.
    try:
        im = Image.open(io.BytesIO(photo))  # reads the header only
        # The full size, before draft(): a 10 MB PNG or JPEG can hold 100M+ pixels, at several bytes each.
        if im.width * im.height > MAX_PIXELS:
            raise BadPhoto(f"{im.width}x{im.height} is too many pixels")
        im.draft("RGB", (1280, 1280))  # so the check below decodes JPEGs at a fraction of the size
        im.load()  # a truncated or corrupt image fails here, as a 400
    except (OSError, Image.DecompressionBombError) as e:  # covers UnidentifiedImageError
        raise BadPhoto(str(e)) from e

    # Not inside the try: urllib's errors are OSErrors too, and a 400 makes the page delete the photo.
    start, timings = time.perf_counter(), {}
    token = _timings.set(timings)
    try:
        result = search_example.lookup(photo)
    finally:
        _timings.reset(token)
    if result["degraded"] and not result["talks"]:
        # No inference, no words and no fingerprint: a 503 keeps the photo queued, where an
        # empty answer would make the page delete it.
        raise Throttled("inference unavailable and nothing else matched")
    for t in result["talks"]:
        t.update(names({"title": t["title"], "speakers_text": t["speakers"]}))
        for m in t["moments"]:
            m["n"] = int(m["id"][-3:])  # still ids end in the slide's number
    out = {k: result[k] for k in ("talks", "found", "degraded", "fingerprint")}
    out["meta"] = _observe(result, timings, time.perf_counter() - start)
    return out


def _observe(result, timings, total):
    """Timings and the trace ID for the results page; the match on the request's span."""
    meta = {"ocr_ms": round(1000 * timings.get("ocr", 0.0)), "search_ms": round(1000 * timings.get("search", 0.0)),
            "total_ms": round(1000 * total)}
    if not trace:
        return meta
    span = trace.get_current_span()
    if span.get_span_context().is_valid:
        meta["trace_id"] = format(span.get_span_context().trace_id, "032x")
    talks = result["talks"]
    span.set_attribute("slidekick.match.talks", len(talks))
    span.set_attribute("slidekick.match.found", result["found"])
    span.set_attribute("slidekick.match.degraded", result["degraded"])
    span.set_attribute("slidekick.match.fingerprint_cosine", result["fingerprint"]["cosine"])
    if talks:
        span.set_attribute("slidekick.match.talk_id", talks[0]["talk_id"])
        span.set_attribute("slidekick.match.score", talks[0]["score"])
        # The lead over the runner-up is the confidence: a close second means a coin flip.
        span.set_attribute("slidekick.match.gap", talks[0]["score"] - (talks[1]["score"] if len(talks) > 1 else 0.0))
    return meta


def names(source):
    """The title without its " - <speakers>" suffix, and the speakers ("" when the parse failed)."""
    speakers = source.get("speakers_text") or ""
    title = source["title"].removesuffix(" - " + speakers) if speakers else source["title"]
    return {"title": title, "speakers": speakers}


# Stills and talks don't change while the app runs, so keep the recent ones in memory.
@functools.lru_cache(maxsize=256)
def slide(still_id):
    """A still as (media type, image bytes). Raises urllib's HTTPError 404 for an unknown id."""
    doc = es("GET", f"/{INDEX}/_doc/{still_id}?_source_includes=slide")
    header, data = doc["_source"]["slide"]["value"].split(",", 1)  # data:image/jpeg;base64,...
    return header.removeprefix("data:").removesuffix(";base64"), base64.b64decode(data)


@functools.lru_cache(maxsize=512)
def talk(talk_id):
    """A talk's length and its slides in order, or None. Treat the result as read-only: it's cached."""
    hits = es("POST", f"/{INDEX}/_search", {
        "size": 100,  # at most 60 stills a talk, plus the talk itself
        "_source": {"includes": ["source", "title", "speakers_text", "duration_sec", "n", "start_sec", "url"],
                    "excludes": ["slide", "said", "abstract"]},
        "query": {"term": {"talk_id": talk_id}},
        "sort": [{"n": {"order": "asc", "missing": "_first"}}],
    })["hits"]["hits"]
    info = next((h["_source"] for h in hits if h["_source"]["source"] == "talk"), None)
    if info is None:
        return None
    return {
        "talk_id": talk_id,
        **names(info),
        "duration_sec": info.get("duration_sec"),
        "url": info["url"],
        "slides": [{"id": h["_id"], "n": h["_source"]["n"], "start_sec": h["_source"]["start_sec"],
                    "url": h["_source"]["url"]} for h in hits if h["_source"]["source"] == "frame"],
    }


def warm_up():
    # The first image embedding after a quiet spell took up to 13 s. Pay that at startup.
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), "gray").save(buf, "JPEG")
    try:
        lookup(buf.getvalue())
    except Exception:
        log.exception("search warm-up failed")
