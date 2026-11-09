"""Phone photo in, talks and YouTube moments out. docs/search.md explains the index and
the query; docs/search_example.py is the reference this follows."""
import base64
import io
import json
import logging
import os
import threading
import urllib.request

from PIL import Image, ImageOps
from rapidocr_onnxruntime import RapidOCR

INDEX = "kubecon-moments"  # alias; points at the current index version
EVENT = "kubecon-na-2024"
MAX_BYTES = 900_000  # the semantic field takes at most 1 MB of decoded image bytes
MAX_PIXELS = 25_000_000  # the page sends at most 1280 px a side, about 1.6M pixels
FIELDS = ["talk_id", "title", "speakers_text", "start_sec", "url"]

log = logging.getLogger("slidekick.search")

# Loading the models takes about a second, so do it once. Lookups run in a thread pool,
# and the engine isn't documented as thread-safe, so one OCR at a time (about 0.5 s).
ocr = RapidOCR()
ocr_lock = threading.Lock()


def configured():
    return bool(os.environ.get("ELASTIC_URL") and os.environ.get("ELASTIC_ES_API_KEY"))


def es(method, path, body):
    req = urllib.request.Request(
        os.environ["ELASTIC_URL"].rstrip("/") + path,
        method=method,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": "ApiKey " + os.environ["ELASTIC_ES_API_KEY"],
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


class BadPhoto(ValueError):
    """The upload isn't an image Pillow can read, or has too many pixels to decode safely."""


def prepare(photo):
    """Upright JPEG under the field's limit, as the semantic field's image object."""
    try:
        im = Image.open(io.BytesIO(photo))  # reads the header only
        im.draft("RGB", (1280, 1280))  # JPEGs then decode at 1/2, 1/4 or 1/8 scale
        # A 10 MB PNG can hold 100M+ pixels, and decoding takes several bytes per pixel.
        if im.width * im.height > MAX_PIXELS:
            raise BadPhoto(f"{im.width}x{im.height} is too many pixels")
        im = ImageOps.exif_transpose(im).convert("RGB")
    except (OSError, Image.DecompressionBombError) as e:  # covers UnidentifiedImageError
        raise BadPhoto(str(e)) from e
    im.thumbnail((1280, 1280))
    for quality in (85, 70, 55):
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=quality)
        if buf.tell() <= MAX_BYTES:
            break
    jpeg = buf.getvalue()
    value = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()
    return {"type": "image", "format": "base64", "value": value}, jpeg


def lookup(photo, size=50):
    image, jpeg = prepare(photo)
    with ocr_lock:
        result, _ = ocr(jpeg)
    text = " ".join(r[1] for r in result or [])

    # No filter on the kNN leg: only stills have `slide`, and a filter there made the query
    # 3-6x slower. If one is ever needed, it goes inside the knn clause.
    retrievers = [{"standard": {"query": {"knn": {
        "field": "slide", "k": 20, "num_candidates": 100,
        "query_vector_builder": {"embedding": {"input": image}},
    }}}}]
    if text:
        frames = [{"term": {"source": "frame"}}, {"term": {"event": EVENT}}]
        retrievers += [
            {"standard": {"query": {"bool": {"filter": frames, "must": {
                "multi_match": {"query": text, "fields": ["ocr_text^2", "said_text"]}}}}}},
            {"standard": {"query": {"bool": {"filter": frames, "must": {
                "match": {"slide": text}}}}}},
        ]
    body = {
        "size": size,
        "_source": {"includes": FIELDS, "excludes": ["slide", "said", "abstract"]},
        "retriever": {"rrf": {"retrievers": retrievers, "rank_window_size": 100}}  # >= size
        if len(retrievers) > 1 else retrievers[0],
    }
    hits = es("POST", f"/{INDEX}/_search", body)["hits"]["hits"]

    # Semantic fields don't aggregate, so group by talk here. RRF scores sit close together;
    # summing them per talk is what separates the talks.
    talks = {}
    for h in hits:
        s = h["_source"]
        speakers = s.get("speakers_text") or ""
        t = talks.setdefault(s["talk_id"], {
            "talk_id": s["talk_id"],
            "title": s["title"].removesuffix(" - " + speakers) if speakers else s["title"],
            "speakers": speakers,
            "score": 0.0,
            "moments": [],
        })
        t["score"] += h["_score"]
        t["moments"].append({"id": h["_id"], "start_sec": s["start_sec"], "url": s["url"]})
    return {"talks": sorted(talks.values(), key=lambda t: t["score"], reverse=True)[:3]}


def warm_up():
    # The first image embedding after a quiet spell took up to 13 s. Pay that at startup.
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), "gray").save(buf, "JPEG")
    try:
        lookup(buf.getvalue())
    except Exception:
        log.exception("search warm-up failed")
