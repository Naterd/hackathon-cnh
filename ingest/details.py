"""Extras the app needs that involve no embeddings, in their own index, kubecon-details.

Per still: a 320 px thumbnail for the deck strip, and the slide's title for labels and alt text.
Per talk: the Sched slot and room, structured speakers, a clean title and the deck links (ingest/sched.py).
Ids match kubecon-moments (<vid>-NNN, <vid>-talk), so the app fetches extras with one _mget.
Why not add these fields to kubecon-moments: any update there re-runs inference on the whole document,
and while Elastic throttled us it stored the document without its vectors (seen Nov 9).

  thumbs   one pod per talk (Indexed Job): writes <derived>/<id>/details.json
  details  one pod: loads every details.json and the Sched file into the index
"""
import argparse
import base64
import concurrent.futures as cf
import io
import json
import os
import re
import sys
import time
from pathlib import Path

from PIL import Image

from index import ES, log

THUMB_PX, THUMB_QUALITY = 320, 70
LOGO = re.compile(r"kube|cloudnat|nativecon|america|20\d\d", re.I)  # the event logo in a slide's top corner
MAPPINGS = {"properties": {
    "source": {"type": "keyword"}, "event": {"type": "keyword"}, "talk_id": {"type": "keyword"},
    "n": {"type": "integer"}, "start_sec": {"type": "float"},
    "thumb": {"type": "binary"}, "slide_title": {"type": "text"},
    "title_clean": {"type": "text", "fields": {"raw": {"type": "keyword", "ignore_above": 512}}},
    "starts_at": {"type": "date"}, "ends_at": {"type": "date"},
    "room": {"type": "keyword"}, "track": {"type": "keyword"},
    "speakers": {"properties": {"name": {"type": "text", "fields": {"raw": {"type": "keyword"}}},
                                "position": {"type": "text"}, "company": {"type": "keyword"}}},
    "decks": {"type": "object", "enabled": False}, "sched_url": {"type": "keyword", "index": False}}}

_ocr = None


def _init():
    global _ocr
    from rapidocr_onnxruntime import RapidOCR
    _ocr = RapidOCR()


def slide_title(path):
    """The biggest line of text in the top third, without the event logo: usually the slide's title."""
    im_h = Image.open(path).height
    lines = []
    for box, text, conf in (_ocr(str(path))[0] or []):
        ys = [p[1] for p in box]
        if min(ys) < im_h / 3 and float(conf) > 0.6 and len(re.findall(r"[A-Za-z]", text)) >= 3 and not LOGO.search(text):
            lines.append((max(ys) - min(ys), min(ys), min(p[0] for p in box), text))
    if not lines:
        return ""
    tallest = max(h for h, *_ in lines)
    row = min(y for h, y, *_ in lines if h >= 0.85 * tallest)
    # a title often comes back as several boxes on one row: join them left to right
    same_row = [l for l in lines if l[0] >= 0.6 * tallest and abs(l[1] - row) < tallest / 2]
    title = " ".join(l[3] for l in sorted(same_row, key=lambda l: l[2]))
    # OCR drops spaces in big type ("WhichSolutionIsBestForYou?"): split long words at case changes,
    # leaving short names like OpenTelemetry alone
    return " ".join(re.sub(r"(?<=[a-z])(?=[A-Z])", " ", w) if len(w) > 15 else w for w in title.split())[:200]


def thumb(path):
    im = Image.open(path).convert("RGB")
    im.thumbnail((THUMB_PX, THUMB_PX))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=THUMB_QUALITY, optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


def one_still(talk_dir, seg):
    path = Path(talk_dir) / seg["still"]
    return {"n": seg["n"], "start_sec": seg["start"], "thumb": thumb(path), "slide_title": slide_title(path)}


def thumbs(a):
    vid = a.talk or Path(a.talks).read_text().split()[int(os.environ["JOB_COMPLETION_INDEX"])]
    d = Path(a.derived) / vid
    out = d / "details.json"
    if out.exists() and not a.force:
        log(f"{vid}: done already")
        return
    segs = json.loads((d / "segments.json").read_text())
    t0 = time.time()
    with cf.ProcessPoolExecutor(int(os.environ.get("WORKERS", 2)), initializer=_init) as ex:
        stills = list(ex.map(one_still, [d] * len(segs), segs))
    out.write_text(json.dumps({"talk_id": vid, "stills": stills}))
    log(f"{vid}: {len(stills)} stills in {time.time() - t0:.0f}s, "
        f"{sum(bool(s['slide_title']) for s in stills)} with a title, "
        f"thumbs {sum(len(s['thumb']) for s in stills) * 3 // 4 // max(1, len(stills)) // 1024} KB each")


def load(a):
    es = ES()
    status, _ = es.call("HEAD", f"/{a.index}")
    if status == 404:
        status, body = es.call("PUT", f"/{a.index}", {"mappings": MAPPINGS})
        if status != 200:
            sys.exit(f"create {a.index}: HTTP {status} {str(body)[:300]}")
    sched = json.loads(Path(a.sched).read_text()) if a.sched and Path(a.sched).exists() else {}
    docs = [(f"{vid}-talk", {"source": "talk", "event": a.event, "talk_id": vid, **s}) for vid, s in sched.items()]
    for f in sorted(Path(a.derived).glob("*/details.json")):
        talk = json.loads(f.read_text())
        docs += [(f"{talk['talk_id']}-{s['n']:03d}", {"source": "frame", "event": a.event, "talk_id": talk["talk_id"], **s})
                 for s in talk["stills"]]
    failed = 0
    for i in range(0, len(docs), 500):
        lines = []
        for _id, doc in docs[i:i + 500]:
            lines += [json.dumps({"index": {"_index": a.index, "_id": _id}}), json.dumps(doc)]
        status, r = es.call("POST", "/_bulk", ("\n".join(lines) + "\n").encode(), ndjson=True)
        if status != 200:
            sys.exit(f"bulk: HTTP {status} {str(r)[:300]}")
        errs = [it["index"]["error"] for it in r["items"] if "error" in it["index"]]
        failed += len(errs)
        if errs:
            log(f"bulk errors, first: {str(errs[0])[:300]}")
    log(f"loaded {len(docs) - failed}/{len(docs)} docs into {a.index}: {len(sched)} talks with Sched data")
    if failed:
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["thumbs", "details"])
    ap.add_argument("--derived", required=True, help="extract.py output: <id>/segments.json and stills/")
    ap.add_argument("--talks", help="one video id per line; thumbs uses line JOB_COMPLETION_INDEX")
    ap.add_argument("--talk", help="one video id, instead of --talks")
    ap.add_argument("--sched", help="ingest/sched.py output")
    ap.add_argument("--event", default="kubecon-na-2024")
    ap.add_argument("--index", default="kubecon-details")
    ap.add_argument("--force", action="store_true", default=os.environ.get("FORCE") == "1")
    a = ap.parse_args()
    thumbs(a) if a.step == "thumbs" else load(a)


if __name__ == "__main__":
    main()
