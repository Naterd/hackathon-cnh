"""Phone photo in, talks and YouTube moments out. docs/search.md explains the index and
the query. The lookup is docs/search_example.py's, which ingest/eval.py measures."""
import base64
import functools
import io
import json
import logging
import os
import sys
import threading
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image
from rapidocr_onnxruntime import RapidOCR

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
    with ocr_lock:
        result, _ = ocr(img)
    return [(text, *np.mean(box, axis=0)) for box, text, _ in (result or [])]


search_example.ocr = one_ocr
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
    result = search_example.lookup(photo)
    if result["degraded"] and not result["talks"]:
        # No inference, no words and no fingerprint: a 503 keeps the photo queued, where an
        # empty answer would make the page delete it.
        raise Throttled("inference unavailable and nothing else matched")
    for t in result["talks"]:
        t.update(names({"title": t["title"], "speakers_text": t["speakers"]}))
        for m in t["moments"]:
            m["n"] = int(m["id"][-3:])  # still ids end in the slide's number
    return {k: result[k] for k in ("talks", "found", "degraded", "fingerprint")}


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
