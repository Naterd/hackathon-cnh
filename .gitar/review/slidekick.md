# SlideKick review rules

Project-specific things to check on every PR. SlideKick is a FastAPI app with
server-rendered pages and a plain-JavaScript PWA, plus ingest Jobs, deployed to
GKE by Argo CD. The repo and the image are public.

## Secrets

- Flag any credential in code, config, the Dockerfile, a build argument, a test
  fixture or a log line: Elasticsearch URLs with keys, API keys, the Dash0 token,
  kubeconfigs, GCP service-account JSON. Secrets come only from Kubernetes
  Secrets (`app-credentials` for the app, `ingest/elastic` for ingest Jobs) at
  runtime, as environment variables or mounted files.
- Flag code that logs or returns a whole settings object or request headers,
  since those can carry an API key.

## The app contract

Any change to `app/`, the `Dockerfile` or `deploy/` must keep all of these:

- The `Dockerfile` stays at the repo root.
- The app listens on port 8080 and serves `/healthz` and `/readyz` with HTTP 200.
  `/readyz` must not call Elasticsearch or anything else that can be slow, or a
  backend blip takes every pod out of the Service.
- The container runs as user 1000, by number. The Deployment keeps
  `runAsNonRoot`, `allowPrivilegeEscalation: false`, dropped capabilities and
  CPU and memory requests and limits.
- The app handles SIGTERM and finishes in-flight requests.
- Workloads carry `app.kubernetes.io/name` and `app.kubernetes.io/version`
  labels on the pod template. Dash0 takes the service name and version from them.
- Nobody edits the image `newName`/`newTag` in `deploy/kustomization.yaml` by
  hand. The deploy bot owns them.

## OpenTelemetry (Dash0)

The Dash0 operator injects the OpenTelemetry SDK into Python pods in the
`slidekick` and `ingest` namespaces, and it turns itself off if it finds
OpenTelemetry already installed.

- Flag any `opentelemetry-*` package in requirements files or the Dockerfile,
  including `opentelemetry-api`, `opentelemetry-distro` and instrumentation
  packages, and any `opentelemetry-instrument` wrapper in the start command.
- Custom spans and metrics import `opentelemetry` inside `try/except ImportError`
  and become no-ops without it, as `ingest/telemetry.py` does.
- Flag code that creates a `TracerProvider`, `MeterProvider` or exporter, or
  sets `OTEL_EXPORTER_OTLP_PROTOCOL` to anything but `http/protobuf`.
- Run uvicorn as a single process: no `--workers` and no `--reload` in the
  container. Auto-instrumentation doesn't survive the fork. Scale with replicas.

## Frontend

- Templates keep Jinja autoescaping on. Flag `|safe`, `Markup(...)` or
  `innerHTML` on anything derived from Elasticsearch, OCR text, captions, talk
  metadata or user uploads. Talk titles and captions come from YouTube and are
  untrusted.
- Plain JavaScript, no framework or build step, unless the PR says why.
- When the page sets a Content-Security-Policy, `connect-src` must allow
  `https://ingress.us-west-2.aws.dash0.com` for browser monitoring.
- Photo uploads enforce a maximum size and check the content type before the
  server decodes the image.
- YouTube links use `https://www.youtube.com/watch?v=<id>&t=<seconds>s`. We link
  to the talk and never re-host video.
