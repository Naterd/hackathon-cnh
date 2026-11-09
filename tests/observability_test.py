"""The app's tracing and the lookup telemetry stay wired up. Run from app/:
python ../tests/observability_test.py   (needs opentelemetry-sdk==1.44.0, CI only)"""

import importlib.metadata
import re
import sys
import time
from pathlib import Path

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

APP = Path(".")
failed = 0


def check(ok, what):
    global failed
    failed += not ok
    print(f"{'ok  ' if ok else 'FAIL'} {what}")


# 1. The pin the Dash0 operator needs. fastapi pulls in opentelemetry-api; any version
#    other than the operator's turns tracing off with only a warning in the pod log.
reqs = (APP / "requirements.txt").read_text()
otel = re.findall(r"^(opentelemetry-[\w-]+)\s*([=<>!~].*)?$", reqs, re.M)
check(otel == [("opentelemetry-api", "==1.44.0")], f"requirements.txt pins only opentelemetry-api==1.44.0 (found {otel})")
installed = importlib.metadata.version("opentelemetry-api")
check(installed == "1.44.0", f"installed opentelemetry-api is 1.44.0 (found {installed})")

# 2. The lookup's spans, attributes and meta, with a fake photo and a canned search.
exporter = InMemorySpanExporter()
provider = TracerProvider()
provider.add_span_processor(SimpleSpanProcessor(exporter))
trace.set_tracer_provider(provider)

sys.path.insert(0, ".")
import io  # noqa: E402

from PIL import Image  # noqa: E402

import search  # noqa: E402
import search_example  # noqa: E402

search.ocr = lambda img: ([], None)  # the app's shared engine, called through one_ocr


def fake_reference_lookup(photo):
    # Calls OCR and Elasticsearch through the module names, as search_example.lookup does.
    search_example.ocr(None)
    search_example.es("POST", "/kubecon-moments/_search", {})
    return {"ocr_text": "ApplicationSet", "found": "recording", "degraded": False,
            "fingerprint": {"cosine": 0.91, "lead": 0.3, "sure": False},
            "talks": [{"talk_id": "A", "title": "A", "speakers": "", "score": 0.5,
                       "moments": [{"id": "A-001", "start_sec": 1.0, "end_sec": 2.0, "url": "u"}]},
                      {"talk_id": "B", "title": "B", "speakers": "", "score": 0.2,
                       "moments": [{"id": "B-002", "start_sec": 3.0, "end_sec": 4.0, "url": "u"}]}]}


search_example.lookup = fake_reference_lookup
search._reference_es = lambda *a, **k: {"hits": {"hits": []}}  # what search_example.es wraps

buf = io.BytesIO()
Image.new("RGB", (64, 48), "gray").save(buf, "JPEG")
with trace.get_tracer("test").start_as_current_span("POST /api/lookup") as server:
    result = search.lookup(buf.getvalue())
server_ctx = server.get_span_context()

meta = result.get("meta", {})
check({"ocr_ms", "search_ms", "total_ms"} <= meta.keys(), f"lookup returns meta timings (found {sorted(meta)})")
check(meta.get("trace_id") == format(server_ctx.trace_id, "032x"), "meta.trace_id is the request's trace ID")
check([t["talk_id"] for t in result["talks"]] == ["A", "B"], "talks pass through unchanged")

spans = {s.name: s for s in exporter.get_finished_spans()}
for name in ("lookup.ocr", "lookup.search"):
    s = spans.get(name)
    check(s is not None and s.parent is not None and s.parent.span_id == server_ctx.span_id,
          f"{name} is a child span of the request")
attrs = dict(spans["POST /api/lookup"].attributes)
for key in ("slidekick.match.talk_id", "slidekick.match.score", "slidekick.match.gap", "slidekick.match.talks",
            "slidekick.match.found", "slidekick.match.degraded", "slidekick.match.fingerprint_cosine"):
    check(key in attrs, f"request span has {key}")
check(attrs.get("slidekick.match.talk_id") == "A", "slidekick.match.talk_id is the top talk")

# 3. The page still shows it.
html = (APP / "templates/index.html").read_text()
js = (APP / "static/app.js").read_text()
check('id="match-timing"' in html, "index.html has #match-timing")
check(re.search(r"const \{ talks,[^}]*\bmeta\b", js) and re.search(r"Object\.assign\(photo, \{[^}]*\bmeta\b", js), "app.js keeps meta with the photo")
check('$("match-timing")' in js and "trace_id" in js, "app.js renders the timing line and trace ID")

sys.exit(1 if failed else 0)
