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
import search  # noqa: E402

search.prepare = lambda photo: ({"type": "image"}, b"jpeg")
search.ocr = lambda jpeg: ([[None, "ApplicationSet"]], None)
search.names = lambda s: {"title": s["talk_id"], "speakers": ""}
hits = [{"_id": f"s{i}", "_score": score, "_source": {"talk_id": talk, "n": i, "start_sec": 10.0 * i, "url": "u"}}
        for i, (talk, score) in enumerate([("A", 0.5), ("A", 0.3), ("B", 0.2)])]
search.es = lambda *a, **k: {"hits": {"hits": hits}}

with trace.get_tracer("test").start_as_current_span("POST /api/lookup") as server:
    result = search.lookup(b"photo")
server_ctx = server.get_span_context()

meta = result.get("meta", {})
check({"ocr_ms", "search_ms", "total_ms"} <= meta.keys(), f"lookup returns meta timings (found {sorted(meta)})")
check(meta.get("trace_id") == format(server_ctx.trace_id, "032x"), "meta.trace_id is the request's trace ID")
check([t["talk_id"] for t in result["talks"]] == ["A", "B"], "talks are still grouped and ranked")

spans = {s.name: s for s in exporter.get_finished_spans()}
for name in ("lookup.ocr", "lookup.search"):
    s = spans.get(name)
    check(s is not None and s.parent is not None and s.parent.span_id == server_ctx.span_id,
          f"{name} is a child span of the request")
attrs = dict(spans["POST /api/lookup"].attributes)
for key in ("slidekick.match.talk_id", "slidekick.match.score", "slidekick.match.gap", "slidekick.match.talks"):
    check(key in attrs, f"request span has {key}")
check(attrs.get("slidekick.match.talk_id") == "A", "slidekick.match.talk_id is the top talk")

# 3. The page still shows it.
html = (APP / "templates/index.html").read_text()
js = (APP / "static/app.js").read_text()
check('id="match-timing"' in html, "index.html has #match-timing")
check("const { talks, meta }" in js and 'pick: 0, meta' in js, "app.js keeps meta with the photo")
check('$("match-timing")' in js and "trace_id" in js, "app.js renders the timing line and trace ID")

sys.exit(1 if failed else 0)
