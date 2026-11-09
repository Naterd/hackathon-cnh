"""Reference lookup against the SlideKick index: phone photo in, talk and YouTube second out.
See docs/search.md for the index and the reasoning. ingest/eval.py measures this exact code
(ingest/run.sh eval), so a change here shows up in the accuracy numbers in docs/results.md.

    pip install pillow rapidocr-onnxruntime==1.4.4   # brings numpy and opencv-python
    ELASTIC_URL=... ELASTIC_ES_API_KEY=... python docs/search_example.py docs/test-photos/appset-603s.jpg
"""
import base64
import io
import json
import os
import sys
import threading
import urllib.request

import cv2
import numpy as np
from PIL import Image, ImageOps

INDEX = "kubecon-moments"  # alias; points at the current index version
MAX_BYTES = 900_000        # the semantic field takes at most 1 MB of decoded image bytes
# In a 1280x720 KubeCon NA 2024 recording, most talks show the slide in this box (x, y, w, h), with the
# KubeCon logo in the panel beside it (x0, y0, x1, y1). ingest/extract.py indexed just the box for those talks.
SLIDE_BOX = (11, 109, 836, 474)
LOGO_PANEL = (870, 300, 1280, 620)
LOGO_WORDS = ("kubec", "ubecon", "nativec", "cloudnat", "america")  # OCR misreads it often: KubeCen, SubeCon
_local = threading.local()


def es(method, path, body=None):
    req = urllib.request.Request(os.environ["ELASTIC_URL"].rstrip("/") + path, method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": "ApiKey " + os.environ["ELASTIC_ES_API_KEY"],
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def find_screen(img):
    """Corners (tl, tr, br, bl) of the screen in a photo, or None: the innermost large four-sided outline."""
    gray = cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    edges = cv2.dilate(cv2.Canny(gray, 20, 60), None)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    quads = []
    for c in contours:
        if cv2.contourArea(c) < 0.15 * gray.size:
            continue
        hull = cv2.convexHull(c)  # glare and blur bend the edges; the hull straightens them
        for eps in (0.02, 0.04, 0.06):
            p = cv2.approxPolyDP(hull, eps * cv2.arcLength(hull, True), True)
            if len(p) == 4:
                quads.append(p.reshape(4, 2).astype(np.float32))
                break
    if not quads:
        return None
    big = max(cv2.contourArea(q) for q in quads)
    # a bezel gives two outlines, around the bezel and around the picture: take the picture
    q = min((q for q in quads if cv2.contourArea(q) >= 0.7 * big), key=cv2.contourArea)
    s, d = q.sum(1), np.diff(q, axis=1).ravel()
    return np.float32([q[s.argmin()], q[d.argmin()], q[s.argmax()], q[d.argmax()]])


def ocr(img):
    """Words with their centers. Same engine as ingest, so merged words match the same way on both sides."""
    if not hasattr(_local, "ocr"):  # one engine per thread, created once: 0.5 s per photo warm
        from rapidocr_onnxruntime import RapidOCR
        _local.ocr = RapidOCR()
    res, _ = _local.ocr(img)
    return [(text, *np.mean(box, axis=0)) for box, text, _ in (res or [])]


def image_object(img):
    for quality in (85, 70, 55):
        _, jpeg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if len(jpeg) <= MAX_BYTES:
            break
    return {"type": "image", "format": "base64", "value": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}


def prepare(photo_bytes):
    """Phone photo -> (image object, OCR text, what was found: recording, screen or photo).

    Finds the screen and flattens it. If it's a whole recording (the KubeCon logo beside the slide box),
    it keeps only the slide box, and only the words in it: the logo text would match every keynote still.
    """
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(photo_bytes))).convert("RGB")
    im.thumbnail((1600, 1600))
    img, found = cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2BGR), "photo"
    corners = find_screen(img)
    if corners is not None:
        m = cv2.getPerspectiveTransform(corners, np.float32([[0, 0], [1280, 0], [1280, 720], [0, 720]]))
        img, found = cv2.warpPerspective(img, m, (1280, 720)), "screen"
    words = ocr(img)
    x, y, w, h = SLIDE_BOX
    x0, y0, x1, y1 = LOGO_PANEL
    logo = [t.lower() for t, cx, cy in words if x0 <= cx < x1 and y0 <= cy < y1]
    if found == "screen" and any(k in t for t in logo for k in LOGO_WORDS):
        img, found = img[y:y + h, x:x + w], "recording"
        words = [wd for wd in words if x <= wd[1] < x + w and y <= wd[2] < y + h]
    return image_object(img), " ".join(t for t, _, _ in words), found


def lookup(photo_bytes, event="kubecon-na-2024", size=50, index=INDEX):
    image, text, found = prepare(photo_bytes)
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
    hits = es("POST", f"/{index}/_search", body)["hits"]["hits"]
    talks = {}  # group by talk in code: semantic fields don't aggregate
    for h in hits:
        s = h["_source"]
        t = talks.setdefault(s["talk_id"], {"talk_id": s["talk_id"], "title": s["title"],
                                            "speakers": s.get("speakers_text", ""), "score": 0.0, "moments": []})
        t["score"] += h["_score"]
        t["moments"].append({"id": h["_id"], "start_sec": s["start_sec"], "end_sec": s["end_sec"], "url": s["url"]})
    ranked = sorted(talks.values(), key=lambda t: t["score"], reverse=True)
    return {"ocr_text": text, "found": found, "talks": ranked[:3]}


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
    print("\nbest:", best["title"], best["moments"][0]["url"])
    print("still image:", still_data_url(best["moments"][0]["id"])[:40] + "…")
    print("related:", [r["title"][:50] for r in related(best["talk_id"])][:3])
