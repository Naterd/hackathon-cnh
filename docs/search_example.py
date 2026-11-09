"""Reference lookup against the SlideKick index: phone photo in, talk and YouTube second out.
Tested against the live index on Nov 9. See docs/search.md for the index and the reasoning.

    pip install pillow rapidocr-onnxruntime==1.4.4   # OCR is optional; without it the text legs are skipped
    ELASTIC_URL=... ELASTIC_ES_API_KEY=... python docs/search_example.py docs/test-photos/appset-603s.jpg
"""
import base64
import io
import json
import os
import sys
import urllib.request

from PIL import Image, ImageOps

INDEX = "kubecon-moments"  # alias; points at the current index version
MAX_BYTES = 900_000        # the semantic field takes at most 1 MB of decoded image bytes


def es(method, path, body=None):
    req = urllib.request.Request(os.environ["ELASTIC_URL"].rstrip("/") + path, method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": "ApiKey " + os.environ["ELASTIC_ES_API_KEY"],
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def prepare(photo_bytes):
    """Phone photo -> JPEG under the 1 MB limit, upright, as the semantic field's image object."""
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(photo_bytes))).convert("RGB")
    im.thumbnail((1280, 1280))
    buf = io.BytesIO()
    for quality in (85, 70, 55):
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=quality)
        if buf.tell() <= MAX_BYTES:
            break
    jpeg = buf.getvalue()
    obj = {"type": "image", "format": "base64", "value": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}
    return obj, jpeg


def ocr_text(jpeg):
    """Same OCR engine as ingest, so merged words match the same way on both sides."""
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        return ""
    res, _ = RapidOCR()(jpeg)
    return " ".join(r[1] for r in (res or []))


def lookup(photo_bytes, event="kubecon-na-2024", size=50):
    image, jpeg = prepare(photo_bytes)
    text = ocr_text(jpeg)
    only_frames = [{"term": {"source": "frame"}}, {"term": {"event": event}}]
    # No filter on the kNN leg: only stills have `slide`, and a filter there made this query 3-6x slower
    # (7-13 s instead of 2 s, measured Nov 9). If one is ever needed, it goes inside the knn clause.
    retrievers = [{"standard": {"query": {"knn": {
        "field": "slide", "k": 20, "num_candidates": 100,
        "query_vector_builder": {"embedding": {"input": image}}}}}}]
    if text:
        retrievers += [
            {"standard": {"query": {"bool": {"filter": only_frames, "must": {
                "multi_match": {"query": text, "fields": ["ocr_text^2", "said_text"]}}}}}},
            {"standard": {"query": {"bool": {"filter": only_frames, "must": {"match": {"slide": text}}}}}},
        ]
    body = {"size": size, "_source": {"excludes": ["slide", "said", "abstract"]},
            "retriever": {"rrf": {"retrievers": retrievers, "rank_window_size": 100}}  # must be >= size
            if len(retrievers) > 1 else retrievers[0]}
    hits = es("POST", f"/{INDEX}/_search", body)["hits"]["hits"]
    talks = {}  # group by talk in code: semantic fields don't aggregate
    for h in hits:
        t = talks.setdefault(h["_source"]["talk_id"], {"talk": h["_source"]["title"], "score": 0.0, "moments": []})
        t["score"] += h["_score"]
        t["moments"].append({"id": h["_id"], "start_sec": h["_source"]["start_sec"], "url": h["_source"]["url"]})
    ranked = sorted(talks.values(), key=lambda t: t["score"], reverse=True)
    return {"ocr_text": text, "talks": ranked[:3]}


def still_data_url(doc_id):
    """The matched slide's image, for the result page: one document, so _source may include `slide`."""
    return es("GET", f"/{INDEX}/_doc/{doc_id}?_source_includes=slide")["_source"]["slide"]["value"]


def related(talk_id, size=5):
    """Talks whose description means something similar."""
    talk = es("GET", f"/{INDEX}/_doc/{talk_id}-talk?_source_includes=description")["_source"]
    body = {"size": size + 1, "_source": {"includes": ["talk_id", "title", "url"]},
            "query": {"bool": {"filter": [{"term": {"source": "talk"}}],
                               "must": {"match": {"abstract": talk["description"][:2000]}},
                               "must_not": [{"term": {"talk_id": talk_id}}]}}}
    return [h["_source"] for h in es("POST", f"/{INDEX}/_search", body)["hits"]["hits"]][:size]


if __name__ == "__main__":
    result = lookup(open(sys.argv[1], "rb").read())
    print(json.dumps(result, indent=1)[:1500])
    best = result["talks"][0]
    print("\nbest:", best["talk"], best["moments"][0]["url"])
    print("still image:", still_data_url(best["moments"][0]["id"])[:40] + "…")
    print("related:", [r["title"][:50] for r in related(best["moments"][0]["id"].rsplit("-", 1)[0])][:3])
