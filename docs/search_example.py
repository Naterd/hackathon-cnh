"""Reference lookup against the SlideKick index: phone photo in, talk and YouTube second out.
See docs/search.md for the index and the reasoning. ingest/eval.py measures this exact code
(ingest/run.sh eval), so a change here shows up in the accuracy numbers in docs/results.md.
Needs layout-kubecon-na-2024.png next to this file.

    pip install pillow rapidocr-onnxruntime==1.4.4   # brings numpy and opencv-python
    ELASTIC_URL=... ELASTIC_ES_API_KEY=... python docs/search_example.py docs/test-photos/appset-603s.jpg
"""
import base64
import io
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

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
# Everything around the slide and the speaker camera is the same picture in every boxed recording: the
# median of 200 frames from 50 talks, in grey, with the parts that change (slide, camera) at alpha 0.
LAYOUT = Path(__file__).with_name("layout-kubecon-na-2024.png")
FRAME = np.float32([[0, 0], [1280, 0], [1280, 720], [0, 720]])
MIN_INLIERS = 12           # matched points that must agree before we believe the recording is in the photo
# A side index with one pixel fingerprint (`fp`) per still, same ids as INDEX: ingest/details.py builds it.
FP_INDEX = "kubecon-details"
FP_MIN = 0.7               # cosine below this is noise: leave the fingerprint out of the ranking
FP_SURE = (0.8, 0.03)      # cosine, and lead over the best other talk, at which the fingerprint decides alone:
                           # 96% right at this bar on simulated photos against 2,222 stills
_local = threading.local()
_layout_lock, _layout = threading.Lock(), None


class Throttled(Exception):
    """Elasticsearch kept answering 429: the inference service is over its rate limit."""


def es(method, path, body=None, tries=2):
    req = urllib.request.Request(os.environ["ELASTIC_URL"].rstrip("/") + path, method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": "ApiKey " + os.environ["ELASTIC_ES_API_KEY"],
                                          "Content-Type": "application/json"})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
            if attempt == tries - 1:
                raise Throttled(path) from e
            time.sleep(1)  # the limit refills within about a second. A 429 itself takes about 4 s to come
            # back, so more tries make a throttled lookup very slow: 3 tries measured 16 s.


def _sift():
    if not hasattr(_local, "sift"):  # one per thread: OpenCV detectors aren't documented as thread-safe
        _local.sift, _local.clahe = cv2.SIFT_create(contrastThreshold=0.02), cv2.createCLAHE(2.0, (8, 8))
    return _local.sift, _local.clahe


def _layout_features():
    """Keypoints (in 1280x720 frame coordinates) and descriptors of the recording's fixed surround."""
    global _layout
    with _layout_lock:
        if _layout is None:
            tpl = cv2.imread(str(LAYOUT), cv2.IMREAD_UNCHANGED)
            if tpl is None:
                print(f"warning: {LAYOUT} is missing, so recordings can't be located in photos", file=sys.stderr)
                _layout = (np.zeros((0, 2), np.float32), np.zeros((0, 128), np.float32))
                return _layout
            pts, des = [], []
            for scale in (1.0, 0.5):  # photos usually show the frame smaller than 1280 px
                g = cv2.resize(tpl[..., 0], None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                m = cv2.resize(tpl[..., 3], (g.shape[1], g.shape[0]), interpolation=cv2.INTER_NEAREST)
                k, d = _sift()[0].detectAndCompute(g, m)
                pts += [np.float32(kp.pt) / scale for kp in k]
                des.append(d)
            _layout = (np.float32(pts), np.vstack(des))
    return _layout


def _match_layout(img):
    """Homography from the 1280x720 recording frame to img, and how many matched points agree with it."""
    pts, des = _layout_features()
    sift, clahe = _sift()
    k, d = sift.detectAndCompute(clahe.apply(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)), None)  # CLAHE: washed-out projections
    if d is None or len(k) < 8 or not len(des):
        return None, 0
    pairs = cv2.BFMatcher(cv2.NORM_L2).knnMatch(des, d, k=2)
    good = [p[0] for p in pairs if len(p) == 2 and p[0].distance < 0.8 * p[1].distance]
    if len(good) < 8:
        return None, len(good)
    h, inliers = cv2.findHomography(np.float32([pts[m.queryIdx] for m in good]),
                                    np.float32([k[m.trainIdx].pt for m in good]), cv2.USAC_MAGSAC, 4.0)
    n = int(inliers.sum()) if inliers is not None else 0
    return (h, n) if h is not None and n >= MIN_INLIERS else (None, n)


def find_recording(img, pad=80):
    """Where a boxed KubeCon recording sits in a photo: the homography from the 1280x720 frame, or None.

    It matches the recording's fixed surround (logo, mountains, box outlines), so it doesn't need to see the
    screen's edges: a projector screen with a white margin, a browser window around the video, room clutter
    and a head in front all work. Measured on simulated photos (docs/results.md), within 3% of the true
    corners: laptop 97%, projector 86%, projector with the video in a browser window 54%. find_screen
    alone: 93%, 21%, 0%.
    """
    h, n = _match_layout(img)
    if h is None:
        return None
    # Second pass on the flattened frame: same scale and angle as the template, so more points match.
    shift = np.float64([[1, 0, pad], [0, 1, pad], [0, 0, 1]])
    flat = cv2.warpPerspective(img, shift @ np.linalg.inv(h), (1280 + 2 * pad, 720 + 2 * pad))
    h2, n2 = _match_layout(flat)
    if h2 is not None and n2 >= n:
        h = h @ np.linalg.inv(shift) @ h2
    quad = cv2.perspectiveTransform(FRAME.reshape(1, 4, 2), h)[0]
    if not cv2.isContourConvex(quad) or cv2.contourArea(quad) < 0.005 * img.shape[0] * img.shape[1]:
        return None
    return h


def find_screen(img):
    """Corners (tl, tr, br, bl) of the screen in a photo, or None: the innermost large four-sided outline.
    The fallback for photos find_recording can't place: stage keynotes, and slides photographed directly."""
    blur = cv2.GaussianBlur(img, (5, 5), 0)
    # Edges per colour channel: a lit wall and a washed-out projection can have the same grey level.
    edges = cv2.dilate(np.max([cv2.Canny(blur[..., c], 20, 60) for c in range(3)], axis=0), None)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    height, width = img.shape[:2]
    quads = []
    for c in contours:
        if cv2.contourArea(c) < 0.04 * height * width:  # a projector screen from the back rows is small
            continue
        hull = cv2.convexHull(c)  # glare and blur bend the edges; the hull straightens them
        for eps in (0.02, 0.04, 0.06):
            p = cv2.approxPolyDP(hull, eps * cv2.arcLength(hull, True), True)
            if len(p) == 4:
                q = _ordered(p.reshape(4, 2).astype(np.float32))
                top, right, bottom, left = (np.linalg.norm(q[i] - q[(i + 1) % 4]) for i in range(4))
                on_border = sum(x < 3 or y < 3 or x > width - 4 or y > height - 4 for x, y in q)
                # screen-shaped, and not the photo's own frame
                if 1.2 < (top + bottom) / (left + right + 1e-6) < 2.6 and on_border < 3:
                    quads.append(q)
                break
    if not quads:
        return None
    big = max(cv2.contourArea(q) for q in quads)
    # a bezel gives two outlines, around the bezel and around the picture: take the picture
    return min((q for q in quads if cv2.contourArea(q) >= 0.7 * big), key=cv2.contourArea)


def _ordered(q):
    s, d = q.sum(1), np.diff(q, axis=1).ravel()
    return np.float32([q[s.argmin()], q[d.argmin()], q[s.argmax()], q[d.argmax()]])


def trim_dark_border(flat, lum=60, frac=0.35):
    """Cut a kept bezel or letterbox bars off a flattened screen: edge rows and columns that are mostly dark."""
    g = cv2.cvtColor(flat, cv2.COLOR_BGR2GRAY)
    h, w = g.shape
    rows, cols = (g < lum).mean(1) > 1 - frac, (g < lum).mean(0) > 1 - frac

    def run(dark, limit):
        n = 0
        while n < limit and dark[n]:
            n += 1
        return n
    top, bottom, left, right = run(rows, h // 4), run(rows[::-1], h // 4), run(cols, w // 4), run(cols[::-1], w // 4)
    if not top + bottom + left + right:
        return flat
    return cv2.resize(flat[top:h - bottom, left:w - right], (w, h))


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


def flatten(photo_bytes):
    """Phone photo -> (image, OCR text, what was found: recording, screen or photo).

    recording: a boxed KubeCon recording is in the photo. Keep only the slide box, which is what the index
               holds for those talks, cut from the full-size photo so small text survives.
    screen:    some other screen is in the photo: flatten it.
    photo:     neither: use the photo as it is.
    """
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(photo_bytes))).convert("RGB")
    im.thumbnail((3000, 3000))
    full = cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2BGR)
    s = min(1.0, 1600 / max(full.shape[:2]))  # find things on a smaller copy, cut them from the full photo
    img = cv2.resize(full, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else full
    up = np.diag([1 / s, 1 / s, 1.0])
    x, y, w, h = SLIDE_BOX
    hom = find_recording(img)
    if hom is not None:
        box = cv2.perspectiveTransform(np.float32([[x, y], [x + w, y], [x + w, y + h], [x, y + h]]).reshape(1, 4, 2), up @ hom)[0]
        zoom = float(np.clip(np.linalg.norm(box[1] - box[0]) / w, 1, 2))  # keep the photo's detail for OCR, up to 2x
        size = (round(w * zoom), round(h * zoom))
        m = cv2.getPerspectiveTransform(box, np.float32([[0, 0], [size[0], 0], [size[0], size[1]], [0, size[1]]]))
        big = cv2.warpPerspective(full, m, size)
        words = ocr(big)
        img = cv2.resize(big, (w, h), interpolation=cv2.INTER_AREA)  # the size of the indexed stills
        return img, " ".join(t for t, _, _ in words), "recording"
    found, corners = "photo", find_screen(img)
    if corners is not None:
        m = cv2.getPerspectiveTransform(corners / s, FRAME)
        img, found = trim_dark_border(cv2.warpPerspective(full, m, (1280, 720))), "screen"
    words = ocr(img)
    x0, y0, x1, y1 = LOGO_PANEL
    logo = [t.lower() for t, cx, cy in words if x0 <= cx < x1 and y0 <= cy < y1]
    if found == "screen" and any(k in t for t in logo for k in LOGO_WORDS):  # a recording the matcher missed
        img, found = img[y:y + h, x:x + w], "recording"
        words = [wd for wd in words if x <= wd[1] < x + w and y <= wd[2] < y + h]
    else:  # the recording's logo may still be in view, and its text matches every keynote still
        words = [wd for wd in words if not any(k in wd[0].lower() for k in LOGO_WORDS)]
    return img, " ".join(t for t, _, _ in words), found


def prepare(photo_bytes):
    """Phone photo -> (image object for the semantic field, OCR text, what was found)."""
    img, text, found = flatten(photo_bytes)
    return image_object(img), text, found


def fingerprint(img):
    """The image as 576 numbers: 32x18 grey pixels, mean removed, unit length. None for a blank image.
    Must match fingerprint() in ingest/details.py, which made the `fp` vectors in FP_INDEX."""
    g = cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (32, 18), interpolation=cv2.INTER_AREA).astype(np.float32).ravel()
    g -= g.mean()
    norm = float(np.linalg.norm(g))
    return None if norm < 1 else (g / (norm + 1e-6)).tolist()


def fingerprint_hits(img, event, k=20):
    """Stills whose pixels look like img, best first, as (hit, cosine). A plain kNN: no inference call, about
    0.1 s. It only means something when img is lined up with the still, so cosine tells us whether to trust it."""
    vector = fingerprint(img)
    if vector is None:
        return []
    try:
        hits = es("POST", f"/{FP_INDEX}/_search", {
            "size": k, "_source": {"excludes": ["thumb", "fp"]},
            "knn": {"field": "fp", "query_vector": vector, "k": k, "num_candidates": 200}})["hits"]["hits"]
    except (Throttled, urllib.error.URLError, TimeoutError):  # no side index, or it's unreachable: do without
        return []
    # dot_product on unit vectors: Elasticsearch scores it (1 + cosine) / 2
    return [(h, 2 * h["_score"] - 1) for h in hits if h["_source"].get("event", event) == event]


def lookup(photo_bytes, event="kubecon-na-2024", size=50, index=INDEX):
    img, text, found = flatten(photo_bytes)
    only_frames = [{"term": {"source": "frame"}}, {"term": {"event": event}}]
    source = {"excludes": ["slide", "said", "abstract"]}
    keywords = {"bool": {"filter": only_frames, "must": {
        "multi_match": {"query": text, "fields": ["ocr_text^2", "said_text"]}}}}
    # No filter on the kNN leg: only stills have `slide`, and a filter there made this query 3-6x slower
    # (7-13 s instead of 2 s, measured Nov 9). If one is ever needed, it goes inside the knn clause.
    retrievers = [{"standard": {"query": {"knn": {
        "field": "slide", "k": 20, "num_candidates": 100,
        "query_vector_builder": {"embedding": {"input": image_object(img)}}}}}}]
    if text:
        retrievers += [
            {"standard": {"query": keywords}},
            {"standard": {"query": {"bool": {"filter": only_frames, "must": {"match": {"slide": text}}}}}},
        ]
    body = {"size": size, "_source": source,
            "retriever": {"rrf": {"retrievers": retrievers, "rank_window_size": 100}}  # must be >= size
            if len(retrievers) > 1 else retrievers[0]}

    # The pixel fingerprint first: it takes 0.1 s and no inference call.
    fp = fingerprint_hits(img, event) if found != "photo" else []
    cosine = fp[0][1] if fp else 0.0
    best_talk = fp[0][0]["_source"]["talk_id"] if fp else None
    lead = cosine - max([c for h, c in fp if h["_source"]["talk_id"] != best_talk], default=0.0)
    sure = cosine >= FP_SURE[0] and lead >= FP_SURE[1]

    degraded, hits = False, []
    if not sure:  # when the photo lines up with one still pixel for pixel, the other legs can't add anything
        try:
            hits = es("POST", f"/{index}/_search", body)["hits"]["hits"]
        except (Throttled, urllib.error.URLError, TimeoutError):
            # The image and text-to-image legs need the inference service. Without it, the slide's words
            # and the fingerprint still find the talk for most photos, with no inference call.
            degraded = True
            hits = es("POST", f"/{index}/_search", {"size": size, "_source": source, "query": keywords})["hits"]["hits"] if text else []

    # Merge the rankings by rank (RRF, k = 60), in code: the fingerprint lives in another index.
    docs, score = {}, {}
    for rank, h in enumerate(hits):
        docs[h["_id"]], score[h["_id"]] = h["_source"], 1 / (61 + rank)
    if cosine >= FP_MIN:
        for rank, (h, _) in enumerate(fp):
            docs.setdefault(h["_id"], h["_source"])
            score[h["_id"]] = score.get(h["_id"], 0.0) + 1 / (61 + rank)

    talks = {}  # group by talk in code: semantic fields don't aggregate
    for doc_id in sorted(score, key=score.get, reverse=True):
        s = docs[doc_id]
        t = talks.setdefault(s["talk_id"], {"talk_id": s["talk_id"], "title": s["title"],
                                            "speakers": s.get("speakers_text", ""), "score": score[doc_id],
                                            "top3": 0.0, "total": 0.0, "moments": []})
        if len(t["moments"]) < 3:
            t["top3"] += score[doc_id]
        t["total"] += score[doc_id]
        t["moments"].append({"id": doc_id, "start_sec": s["start_sec"], "end_sec": s["end_sec"], "url": s["url"]})
    # Rank talks by their best single hit, then by their best three. Summing every hit (`total`) mostly
    # counts hits, because rank scores are nearly flat: ten weak hits beat one still ranked first on every leg.
    ranked = sorted(talks.values(), key=lambda t: (t["score"], t["top3"]), reverse=True)
    return {"ocr_text": text, "found": found, "degraded": degraded,
            "fingerprint": {"cosine": round(cosine, 3), "lead": round(lead, 3), "sure": sure},
            "talks": ranked[:3]}


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
