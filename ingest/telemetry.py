"""Tracing and metrics for the ingest Jobs, exported to Dash0.

The Dash0 operator injects the OpenTelemetry SDK into pods in the ingest
namespace. Don't add OpenTelemetry packages to the ingest image: the operator
disables itself when it finds them. Without the SDK (e.g. locally) every call
here is a no-op.

    from ingest import telemetry

    with telemetry.talk(video_id):
        with telemetry.phase("scene_detect"):
            ...
        telemetry.stills(kept=41, dropped=12)
        with telemetry.phase("index"):
            telemetry.docs(indexed=40, rejected=1)
    telemetry.flush()
"""

import atexit
from contextlib import contextmanager

try:
    from opentelemetry import metrics, trace
    from opentelemetry.trace import Status, StatusCode
except ImportError:
    metrics = trace = None

NAME = "slidekick.ingest"

if trace:
    _tracer = trace.get_tracer(NAME)
    _meter = metrics.get_meter(NAME)
    _stills = _meter.create_counter(
        "slidekick.ingest.stills", unit="{still}",
        description="Slide stills per outcome: kept or dropped as near-duplicates")
    _docs = _meter.create_counter(
        "slidekick.ingest.docs", unit="{document}",
        description="Elasticsearch bulk items per outcome: indexed or rejected")


@contextmanager
def talk(video_id):
    """One span per talk: ingest.talk. Its status is ERROR if the block raises."""
    if not trace:
        yield
        return
    with _tracer.start_as_current_span(
        "ingest.talk",
        attributes={"slidekick.video_id": video_id},
        record_exception=True,
        set_status_on_exception=True,
    ):
        yield


@contextmanager
def phase(name):
    """A child span per pipeline step: ingest.<name>, e.g. ingest.ocr."""
    if not trace:
        yield
        return
    with _tracer.start_as_current_span(
        f"ingest.{name}",
        attributes={"slidekick.phase": name},
        record_exception=True,
        set_status_on_exception=True,
    ):
        yield


def stills(kept, dropped=0):
    _count(_stills if trace else None, "stills", kept=kept, dropped=dropped)


def docs(indexed, rejected=0):
    """Pass the bulk response's per-item results: bulk returns HTTP 200 even when items fail."""
    _count(_docs if trace else None, "docs", indexed=indexed, rejected=rejected)
    if trace and rejected:
        trace.get_current_span().set_status(Status(StatusCode.ERROR, f"{rejected} documents rejected"))


def _count(counter, kind, **outcomes):
    if not counter:
        return
    span = trace.get_current_span()
    for outcome, n in outcomes.items():
        if n:
            counter.add(n, {"outcome": outcome})
        span.set_attribute(f"slidekick.{kind}.{outcome}", n)


def flush(timeout_millis=10000):
    """Export what's buffered. Jobs exit before the 60-second metric export runs."""
    if not trace:
        return
    for provider in (trace.get_tracer_provider(), metrics.get_meter_provider()):
        force_flush = getattr(provider, "force_flush", None)
        if force_flush:
            force_flush(timeout_millis)


atexit.register(flush)
