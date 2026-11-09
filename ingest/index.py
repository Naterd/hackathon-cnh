"""Load extracted talks into Elasticsearch.

Reads <src>/<id>/{talk.json, segments.json, stills/*.jpg} (written by extract.py) and indexes one
document per slide still plus one per talk into <index>. Re-running is safe: document ids are
stable, so documents are overwritten, not duplicated.

  ELASTIC_URL=... ELASTIC_ES_API_KEY=... python index.py --src <dir> --event kubecon-na-2024 \
      --index kubecon-moments-v1 [--alias kubecon-moments] [--limit N]

Throughput measured from GKE: about 75 images a second with 24 parallel bulks of 48, with HTTP 429
beyond that. The default of 16 parallel bulks stays under the limit; throttled items are retried.
"""
import argparse
import base64
import concurrent.futures as cf
import json
import os
import random
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    import telemetry  # next to this script in the Job's /code
except ImportError:
    from ingest import telemetry

MODEL = ".jina-embeddings-v5-omni-small"
MAX_IMAGE_BYTES = 1_000_000  # the semantic field's limit on an image's decoded bytes
SETTINGS = {
    "analysis": {
        # OCR often merges words ("WhichSolutionIsBestForYou?", "NorthAmerica2024"): split on case
        # changes, digits and punctuation, and keep the original token too.
        "filter": {"split_words": {"type": "word_delimiter_graph", "preserve_original": True,
                                   "split_on_case_change": True, "split_on_numerics": True}},
        "analyzer": {
            "slide_text": {"tokenizer": "whitespace",
                           "filter": ["split_words", "flatten_graph", "lowercase", "asciifolding"]},
            "slide_text_search": {"tokenizer": "whitespace",
                                  "filter": ["split_words", "lowercase", "asciifolding"]},
        },
    }
}
SLIDE_TEXT = {"type": "text", "analyzer": "slide_text", "search_analyzer": "slide_text_search"}
MAPPINGS = {
    "properties": {
        "source": {"type": "keyword"},          # frame | talk (later: deck_page, photo, session)
        "event": {"type": "keyword"},
        "talk_id": {"type": "keyword"},
        "video_id": {"type": "keyword"},
        "title": {"type": "text", "fields": {"keyword": {"type": "keyword", "ignore_above": 512}}},
        "speakers_text": {"type": "text"},
        "n": {"type": "integer"},
        "start_sec": {"type": "float"},
        "end_sec": {"type": "float"},
        "url": {"type": "keyword", "index": False},
        "still": {"type": "keyword", "index": False},
        "ocr_text": SLIDE_TEXT,
        "ocr_conf": {"type": "float"},
        "said_text": {"type": "text"},
        "description": {"type": "text"},
        "upload_date": {"type": "date", "format": "yyyyMMdd"},
        "duration_sec": {"type": "float"},
        "caption_source": {"type": "keyword"},
        "slide": {"type": "semantic", "inference_id": MODEL},     # the still, as an image object
        "said": {"type": "semantic", "inference_id": MODEL},      # captions spoken over the slide
        "abstract": {"type": "semantic", "inference_id": MODEL},  # talk description, for related talks
    }
}


def log(msg):
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


class ES:
    def __init__(self):
        self.url = os.environ["ELASTIC_URL"].rstrip("/")
        self.auth = "ApiKey " + os.environ["ELASTIC_ES_API_KEY"]

    def call(self, method, path, body=None, ndjson=False):
        data = body if ndjson or body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.url + path, method=method, data=data, headers={
            "Authorization": self.auth,
            "Content-Type": "application/x-ndjson" if ndjson else "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                code, raw = r.status, r.read()
        except urllib.error.HTTPError as e:
            code, raw = e.code, e.read()
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            return 503, {"error": str(e)}  # transport failure: retryable, like a gateway 503
        try:
            return code, json.loads(raw or b"{}")
        except ValueError:  # gateways answer 502/503/504 with HTML or plain text
            return code, {"error": raw[:300].decode(errors="replace")}


def ensure_index(es, index, alias):
    status, _ = es.call("HEAD", f"/{index}")
    if status == 404:
        status, body = es.call("PUT", f"/{index}", {"settings": SETTINGS, "mappings": MAPPINGS})
        if status != 200:
            raise SystemExit(f"creating {index} failed: {status} {body}")
        log(f"created index {index}")
    if alias:
        status, body = es.call("POST", "/_aliases", {"actions": [
            {"remove": {"index": "*", "alias": alias, "must_exist": False}},
            {"add": {"index": index, "alias": alias}}]})
        log(f"alias {alias} -> {index}: {status}")


def image_object(path):
    data = base64.b64encode(path.read_bytes()).decode()
    return {"type": "image", "format": "base64", "value": f"data:image/jpeg;base64,{data}"}


def talk_docs(talk_dir, event, skipped):
    """(id, doc) pairs for one extracted talk: one talk document, then one per still.
    Stills too big to index are appended to `skipped`, which counts as failures."""
    talk = json.loads((talk_dir / "talk.json").read_text())
    vid = talk["video_id"]
    base = {"event": event, "talk_id": vid, "video_id": vid, "title": talk["title"],
            "speakers_text": talk["speakers_text"], "caption_source": talk.get("caption_source")}
    doc = {**base, "source": "talk", "url": talk["url"], "description": talk["description"],
           "upload_date": talk.get("upload_date"), "duration_sec": talk.get("duration")}
    if talk["description"].strip():
        doc["abstract"] = talk["description"][:4000]
    yield f"{vid}-talk", doc
    for s in json.loads((talk_dir / "segments.json").read_text()):
        still = talk_dir / s["still"]
        if still.stat().st_size > MAX_IMAGE_BYTES:  # extract.py re-encodes; never send one over the limit
            log(f"skipping {vid}/{s['still']}: {still.stat().st_size} bytes is over the 1 MB image limit")
            telemetry.docs(indexed=0, rejected=1)
            skipped.append(f"{vid}/{s['still']}")
            continue
        doc = {**base, "source": "frame", "n": s["n"], "start_sec": s["start"], "end_sec": s["end"],
               "url": f"https://youtu.be/{vid}?t={int(s['start'])}", "still": f"{vid}/{s['still']}",
               "ocr_text": s["ocr_text"], "ocr_conf": s["ocr_conf"], "said_text": s["said"],
               "slide": image_object(still)}
        if s["said"].strip():
            doc["said"] = s["said"]
        yield f"{vid}-{s['n']:03d}", doc


def send(es, index, batch, attempts=6):
    """Bulk-index (id, doc) pairs; retry throttled items with backoff. Returns (ok, failed).
    Bulk answers HTTP 200 even when items fail, so outcomes are counted per item."""
    ok = failed = 0
    for attempt in range(attempts):
        body = "".join(json.dumps({"index": {"_index": index, "_id": i}}) + "\n" + json.dumps(d) + "\n"
                       for i, d in batch).encode()
        status, r = es.call("POST", "/_bulk", body, ndjson=True)
        if status in (429, 502, 503, 504):
            retry = batch
        elif status != 200:
            log(f"bulk failed: HTTP {status} {str(r)[:300]}")
            failed += len(batch)
            retry = []
        else:
            retry = []
            for (i, d), item in zip(batch, r["items"]):
                st = item["index"]["status"]
                if st in (200, 201):
                    ok += 1
                elif st == 429:
                    retry.append((i, d))
                else:
                    failed += 1
                    log(f"doc {i} failed: {st} {str(item['index'].get('error'))[:300]}")
        if not retry:
            break
        batch = retry
        time.sleep(min(30, 2 ** attempt) + random.random())
    else:
        log(f"giving up on {len(batch)} throttled docs after {attempts} attempts")
        failed += len(batch)
    telemetry.docs(indexed=ok, rejected=failed)
    return ok, failed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="directory of extracted <id>/ folders")
    ap.add_argument("--event", default="kubecon-na-2024")
    ap.add_argument("--index", default="kubecon-moments-v1")
    ap.add_argument("--alias", help="point this alias at --index once loading finishes")
    ap.add_argument("--limit", type=int, help="only the first N talks, for quick runs")
    ap.add_argument("--talks", help="comma-separated video ids to load")
    ap.add_argument("--replace", action="store_true",
                    help="delete the talks' existing documents first (a re-extract can produce fewer stills)")
    ap.add_argument("--parallel", type=int, default=int(os.environ.get("PARALLEL", 16)))
    ap.add_argument("--batch", type=int, default=48)
    a = ap.parse_args()

    es = ES()
    ensure_index(es, a.index, None)
    talks = sorted(p.parent for p in Path(a.src).glob("*/segments.json"))
    if a.talks:
        wanted = set(a.talks.split(","))
        talks = [t for t in talks if t.name in wanted]
    if a.limit:
        talks = talks[:a.limit]
    log(f"{len(talks)} talks -> {a.index}, {a.parallel} parallel bulks of {a.batch}")
    if a.replace and talks:
        status, r = es.call("POST", f"/{a.index}/_delete_by_query?refresh=true&conflicts=proceed",
                            {"query": {"terms": {"talk_id": [t.name for t in talks]}}})
        log(f"replace: deleted {r.get('deleted')} existing docs ({status})")

    skipped = []

    def batches():
        batch = []
        for t in talks:
            for pair in talk_docs(t, a.event, skipped):
                batch.append(pair)
                if len(batch) == a.batch:
                    yield batch
                    batch = []
        if batch:
            yield batch

    t0, ok, failed = time.time(), 0, 0
    with telemetry.phase("index"), cf.ThreadPoolExecutor(a.parallel) as ex:
        pending = set()
        for b in batches():  # bounded in-flight work, so memory stays flat
            pending.add(ex.submit(send, es, a.index, b))
            if len(pending) >= a.parallel * 2:
                done, pending = cf.wait(pending, return_when=cf.FIRST_COMPLETED)
                for f in done:
                    o, x = f.result(); ok += o; failed += x
        for f in cf.as_completed(pending):
            o, x = f.result(); ok += o; failed += x
    failed += len(skipped)  # a missing slide is a failure: exit 1 and leave the alias alone
    dt = time.time() - t0
    log(f"indexed {ok} docs, {failed} failed, in {dt:.0f}s ({ok / max(dt, 1):.1f} docs/s)")
    if a.alias and not failed:
        ensure_index(es, a.index, a.alias)
    telemetry.flush()
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
