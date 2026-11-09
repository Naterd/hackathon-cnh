# SlideKick

Photograph a KubeCon slide and get the talk, the speaker and a YouTube link that starts the moment that slide appeared. Upload a camera roll and get your conference day back as a timeline.

Built at the [Cloud Native Hackathon](https://www.wemakedevs.org/hackathons/cnh), Salt Lake City, November 9, 2026. Live at **https://slidekick.us**.

## Status

As of Nov 9, 10:15 MT. The app code starts at kickoff.

| Piece | State |
| --- | --- |
| HTTPS at `slidekick.us` | Live: Google-managed certificate, HTTP redirects to HTTPS. Serves a placeholder until the app ships. `www` is waiting on its DNS record. |
| GKE cluster | Up. App pool: 1–6 × e2-standard-4. Spot ingest pool: 0–32 × e2-standard-8 (up to 256 vCPUs). Argo CD and Argo Rollouts are installed. |
| Elasticsearch | Serverless project `slidekick` (vector-optimized, 9.7). `.jina-embeddings-v5-omni-small` returned the right slide for a simulated phone photo (0.94 against 0.72). Bulk indexing from GKE reaches about 75 images a second. |
| Data | All 370 downloadable KubeCon NA 2024 talks are in GCS: 720p video, metadata and English captions (263 from YouTube, 107 transcribed with Whisper). |
| Dash0 | Operator installed on GKE. Namespaces `slidekick` and `ingest` are monitored, and Kubernetes metrics and logs are flowing. A four-golden-signals dashboard is up. See [Observability (Dash0)](#observability-dash0). |
| App, ingest Jobs, Radar, Gitar | To do |

## How it works

1. **Ingest (GKE spot Jobs).** Crop each recording to its slide area, detect slide changes, drop near-duplicates, OCR each still, and attach the captions spoken while it was on screen. One indexer loads the results into Elasticsearch.
2. **Lookup.** Flatten the phone photo, OCR it, and run one hybrid search. RRF merges image kNN on the multimodal `semantic` field, OCR keyword matching, and text-to-image matching. Results are grouped by talk and returned with a YouTube link at the right second.
3. **Camera roll.** Read each photo's EXIF time in the browser, match every photo, and place it on a timeline. For this week's photos, the session running at that time ranks higher.

Stack: FastAPI with server-rendered pages, a PWA front end in plain JavaScript, Elasticsearch Serverless with Elastic Inference Service, and GKE with Argo CD.

## Observability (Dash0)

Dash0 trial organization, region AWS `us-west-2`, dataset `default`.

| Endpoint | Value |
| --- | --- |
| OTLP gRPC | `ingress.us-west-2.aws.dash0.com:4317` |
| OTLP HTTP | `https://ingress.us-west-2.aws.dash0.com` |
| API | `https://api.us-west-2.aws.dash0.com` |

**On the cluster:**
- The Dash0 operator 0.159.0 runs in namespace `dash0-system` (Helm release `dash0-operator`). Its auth token is in Secret `dash0-system/dash0-authorization-secret`.
- A `Dash0Monitoring` resource in `slidekick` and in `ingest` turns on traces, metrics and pod logs for those namespaces.
- Python auto-instrumentation is on.
- The collector DaemonSet tolerates the spot pool's `workload=ingest:NoSchedule` taint, so ingest Jobs have a node-local collector.

**What the app and ingest code need to do:**
- **Traces come for free.** The operator injects OpenTelemetry into Python 3.10+ pods in monitored namespaces, with no SDK setup in our code. Leave the OTLP protocol at `http/protobuf`.
- **Label the workloads** with `app.kubernetes.io/name` and `app.kubernetes.io/version`. Dash0 takes the service name and version from them, which separates deploys.
- **Add our own signals** with `opentelemetry-api`. Put the match confidence on the lookup span, and count ingest progress: talks indexed, stills per second, Elasticsearch HTTP 400s.
- **Browser monitoring:** add Dash0's web SDK script to `base.html`, and allow `https://ingress.us-west-2.aws.dash0.com` in the page's CSP `connect-src`.
- **The API token never goes in this repo or the image.** The repo is public.

**Ingest telemetry: `ingest/telemetry.py`.** Use it in the ingest code to measure each Job:

```python
from ingest import telemetry

with telemetry.talk(video_id):              # span ingest.talk; ERROR if the block raises
    with telemetry.phase("scene_detect"):   # child span ingest.scene_detect
        ...
    telemetry.stills(kept=41, dropped=12)
    with telemetry.phase("index"):
        telemetry.docs(indexed=40, rejected=1)  # count bulk *items*: bulk returns 200 even when items fail
telemetry.flush()                           # also runs at exit
```

- Use the phase names `read`, `scene_detect`, `stills`, `ocr`, `captions` and `index`, so the dashboard's per-phase panel lines up.
- **Don't add OpenTelemetry packages to the ingest image.** The operator injects the SDK, and turns itself off if it finds OpenTelemetry already installed. Without the SDK, for example run locally, every call is a no-op.
- Label each Job's pod template with `app.kubernetes.io/name: slidekick-ingest`, which becomes its service name in Dash0. Jobs are instrumented when they're created.
- Talks per minute and time per phase come from the spans, so there are no extra metrics for them. The module adds two counters: `slidekick.ingest.stills` and `slidekick.ingest.docs`, each with an `outcome` attribute.

**Getting the API token (for you or your agent).** Use the copy in the cluster, not a chat or a file in the repo. You need `kubectl` access to `cnh`:

```bash
gcloud container clusters get-credentials cnh --zone us-west3-a --project five9-cyclops-playground --dns-endpoint
kubectl -n dash0-system get secret dash0-authorization-secret -o jsonpath='{.data.token}' | base64 -d | sed 's/^/Authorization: Bearer /' > ~/.dash0-auth-header
chmod 600 ~/.dash0-auth-header
curl -s -H @"$HOME/.dash0-auth-header" "https://api.us-west-2.aws.dash0.com/api/dashboards?dataset=default"
```

The header file keeps the token out of command lines, shell history and agent output. Agents: never print the token or paste it into a commit, PR or chat. Any Dash0 API call takes `-H @"$HOME/.dash0-auth-header"` plus `?dataset=default`. For example, PromQL goes to `GET /api/prometheus/api/v1/query?dataset=default&query=…`. The trial token can read and write dashboards, check rules, SLOs and synthetic checks.

**Dashboard: "SlideKick: Four Golden Signals"** has one section for the app and one for ingest, each covering latency, traffic, errors and saturation. A third section, **ingest: pipeline**, is built from `ingest/telemetry.py`: talks per minute by status, time per talk, p95 per phase, documents indexed vs rejected, and stills kept vs dropped. Latency, traffic and errors come from Dash0's span metrics (`dash0_spans_total`, `dash0_spans_duration_seconds_bucket`), filtered by `k8s_namespace_name`, `otel_span_kind` and `otel_span_status_code`. Saturation comes from the pods' CPU and memory against their limits, and from ready nodes. The panels stay empty until instrumented code is running.

- **Open it:** [app.dash0.com](https://app.dash0.com), then **Dashboards** > *SlideKick: Four Golden Signals*.
- **API:** `GET https://api.us-west-2.aws.dash0.com/api/dashboards/slidekick-golden-signals?dataset=default`. To update it, `PUT` the edited JSON to the same URL.

**Gotchas:**
- A namespace's data shows up about 2 minutes after its `Dash0Monitoring` resource is applied.
- Applying `Dash0Monitoring` restarts the pods already running in that namespace, so they get instrumented.
- `/api/v1/series` returns nothing for the span metrics, but `query` does. Don't conclude they're missing from a series lookup.

**Still to do:** a synthetic check on `https://slidekick.us/healthz`, a p95 shutter-to-answer SLO, browser monitoring, and one Agent0 investigation of a real issue for the demo.

## Working in this repo

- **Pull before every commit:** `git pull --rebase --autostash`. Enable the hook that enforces it once per clone: `git config core.hooksPath .githooks`.
- **Deploying:** merge to `main`. The GitHub Action builds the image to GHCR and commits a `deploy: <sha>` tag change. Argo CD on GKE (Application `slidekick`, in namespace `argocd`) then syncs `deploy/` into namespace `slidekick`. The repo and the image must stay public, because the cluster pulls without credentials.
  - **Rollback:** `git revert` the bad commit on `main`. Argo CD deploys the previous state.
  - **Don't `kubectl edit` what Argo CD owns** (Deployment and Service `app`, BackendConfig `app`). Self-heal puts the git version back, so change those in git.
  - The Service is in sync wave 1, so traffic switches only after the new Deployment is healthy. The load balancer's health check is `/readyz` (`deploy/backendconfig.yaml`).
  - **Ingest Jobs aren't deployed by Argo CD.** Run them with `kubectl apply`, because Argo CD's prune and self-heal don't suit Jobs that run to completion.
- **Secrets:** never put them in git, the image or a build argument. They live in Kubernetes Secrets: `app-credentials` for the app, and `ingest/elastic` (Elasticsearch URL and API key) for the ingest Jobs.
- **The app contract:** a Dockerfile at the repo root; the app listens on 8080, serves `/healthz` and `/readyz`, and runs as user 1000. In the cluster it runs in namespace `slidekick` behind Service `app`, port 80.
- **Radar:** the cluster is in our own Radar Cloud organization, not the hackathon's. Connect Claude Code with `claude mcp add --scope user radar-cloud --transport http https://api.radarhq.io/mcp`, then pick our organization when you authenticate. `AGENTS.md` and `.agents/skills/` came with the [Radar starter](https://github.com/skyhook-io/cnh-starter), which describes the hackathon's shared cluster; adjust as we go.

## Layout

| Path | What |
| --- | --- |
| `app/` | The app. Still the starter's Node placeholder, to be replaced by the Python app. |
| `deploy/` | Kubernetes manifests that Argo CD syncs |
| `argocd/` | Argo CD Applications. On our cluster, use `application-byo.yaml`. |
| `.githooks/` | The pull-before-commit hook |
| `.agents/skills/` | Agent skills from the Radar starter |
| `examples/` | Starter examples: canary releases, HPA, Postgres |

## Setup from scratch

1. **Cluster:** a GKE Standard cluster with Workload Identity and the Cloud Storage FUSE driver, plus a tainted spot node pool for ingest. Install Argo CD and Argo Rollouts. Our scripts for this will move into `infra/`.
2. **Edge:** reserve a global static IP, point the domain's A record at it (Cloudflare: DNS only), then apply the Ingress, the ManagedCertificate and the HTTPS-redirect FrontendConfig.
3. **Elasticsearch:** create a vector-optimized Elastic Cloud Serverless project and an API key, and store them as Kubernetes Secrets.
4. **Deploy:** `kubectl apply -f argocd/project.yaml -f argocd/application-byo.yaml`. The project file is needed because this Argo CD has no API server, so its `default` project doesn't exist. From then on, every merge to `main` deploys.

## Data and credits

- Talk recordings: CNCF's KubeCon + CloudNativeCon North America 2024 playlist on YouTube, licensed CC BY. We link to YouTube rather than re-hosting video.
- This week's schedule and decks come from the official KubeCon NA 2026 schedule. Decks are linked, not re-hosted.
- Deploy scaffolding: [skyhook-io/cnh-starter](https://github.com/skyhook-io/cnh-starter).

## AI assistance

We built this with AI coding assistants (Claude Code), as the hackathon rules allow and require us to disclose. The team reviewed and understands the code we submit.
