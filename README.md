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
| Deploys | Live through GitOps (Nov 9, 11:11 MT): a merge to `main` builds the image, and Argo CD (Application `slidekick`) syncs it to namespace `slidekick` within about 3 minutes. |
| Ingest | First run done (Nov 9, ~11:20 MT): Job `ingest-extract` finished 66/66 in 9 min 10 s, and `ingest-index` finished in 76 s. `kubecon-moments` holds 12,516 documents, one per slide still, covering all 370 talks. The Job manifests aren't in the repo yet. |
| Radar, Gitar | Live. See [Sponsor tools](#sponsor-tools). |
| App | FastAPI shell is merged (`app/main.py`, Jinja templates, static JS and CSS). The photo lookup and the camera-roll timeline are still to do. |

## Sponsor tools

Are we using it, what for, and where do you go to see it?

| Tool | Track | What we use it for | Status | Where to look |
| --- | --- | --- | --- | --- |
| **Gitar** (Sonar) | AI code review | Reviews every PR against our rules | Live | The PR's **Checks** tab and the `gitar-bot` comment. Rules in [`.gitar/review/main.md`](.gitar/review/main.md). [app.gitar.ai](https://app.gitar.ai) |
| **Dash0** | Observability | Traces, metrics and logs for the app and ingest; the golden-signals dashboard | Live | [app.dash0.com](https://app.dash0.com) > Dashboards > *SlideKick: Four Golden Signals* |
| **Radar** | Kubernetes UI, AI SRE | Watching and operating the GKE cluster; deploys and investigations from Claude Code | Live | [app.radarhq.io](https://app.radarhq.io), in our own organization, not Cloud Native Hackathon |
| **Elasticsearch** | Search and vector DB | The document store for every slide still, with hybrid search for lookups | Live | [cloud.elastic.co](https://cloud.elastic.co) > Serverless project `slidekick` > Kibana |

### Gitar: AI code review

- **How it's wired:** the Gitar GitHub App is installed on this repo. Every PR gets a `Gitar` check and a `gitar-bot` review comment.
- **What it enforces:** [`.gitar/review/main.md`](.gitar/review/main.md), which lists what blocks a merge: secrets, the app contract, the deploy bot's lines, Elasticsearch semantic-field limits and ingest Job requirements. PR automations would go in `.gitar/rules/`, which is empty so far.
- **How we work with it:** code, manifest and CI changes go through a PR. Read Gitar's findings before merging, and when it misses a project gotcha, add that gotcha to `main.md`. A review can take several minutes; PR #5's check ran for about 4.5.
- **Not yet:** CI-failure diagnosis and auto-apply. These need a CI job that runs on `pull_request`. Our only workflow runs on pushes to `main`, so a PR has no CI for Gitar to fix.

### Dash0: observability

- **How it's wired:** the Dash0 operator in namespace `dash0-system` injects OpenTelemetry into pods in `slidekick` and `ingest`. The app and the ingest Jobs export traces, metrics and logs with no SDK setup in our code. `ingest/telemetry.py` adds per-talk and per-phase spans, plus the stills and documents counters.
- **Where to look:** [app.dash0.com](https://app.dash0.com) > Dashboards > *SlideKick: Four Golden Signals*, which has sections for the app, ingest Jobs, and ingest pipeline. Region `us-west-2`, dataset `default`.
- **Access:** the API token is in Secret `dash0-system/dash0-authorization-secret`. The steps for reading it safely are under [Observability (Dash0)](#observability-dash0).
- **Ingest runs:** the first run (the POC, about 11:20 MT) predates the ingest telemetry, so the dashboard's "ingest: pipeline" panels are empty for it. That's expected. The V2 run is the measured one.
- **Also in Dash0:** synthetic check `slidekick-healthz` and SLO `slidekick-availability` (Dash0 > Synthetic checks, Dash0 > SLOs).
- **Not yet:** see the [checklist](#demo-ready-checklist).

#### How Dash0 helped us solve a problem

1. **The gap.** About 2:40 PM, the dashboard's app panels had nothing in them. Dash0 showed **zero server spans from service `slidekick` in two hours**, though the app was serving traffic: 127 synthetic `/healthz` probes, browser page views, and its logs still arriving. That morning, on the Node starter, the same query showed about 3,500 server spans an hour. Tracing had gone dark silently when the app moved to Python, and nothing alerted on it, because logs kept flowing.
2. **We asked Agent0** why `slidekick` was serving requests but sending no traces ([Agent0 thread](https://app.dash0.com/agent0?org=2f80d046-5fdb-425e-bb24-be79c87eb43d&s=eJydj8sKwjAQRf9l1i32qaE7wY0rQf2BaXNbAzEtTbKQ0n83KFREF-JuuMPMOXeiFVUTKePQjewgtx2MSx6ZPQwwVLWsLSJylxEs95IqQi5SZPkmLrO1iIu0kLFo0zpmbkpkSESZCJojavvGW8izuuLIpgNVxmu95DsfiKo3bzvJji1coEi07LWj6MPupCRqHr9J3obwiUxvQHMwWPFS5zX90eR5_Ct3vgOyNW8n)).
3. **The root cause.** The Dash0 operator's injector had logged it at every pod start, flagged as a telemetry-collection issue: `cannot auto-instrument Python process: dependency conflicts: opentelemetry-api (requires ==1.44.0, found 1.45.2)`. We had never added an OpenTelemetry package. **FastAPI 0.162.2 pulls in `opentelemetry-api` itself** (`uv pip compile` shows `# via fastapi`), so the injector found a newer API than its own 1.44.0 and turned itself off. That's been true since the first Python app ([#7](https://github.com/Naterd/hackathon-cnh/pull/7)).
4. **Agent0 opened the fix itself.** [#21](https://github.com/Naterd/hackathon-cnh/pull/21), opened by Dash0's GitHub App, pins `opentelemetry-api==1.44.0` in `app/requirements.txt`. FastAPI accepts it (it asks for `>=1.44.0`), and it matches the injector exactly. It also rewrote the "no OpenTelemetry packages" rule in `AGENTS.md`, the README, `docs/search.md` and **Gitar's review rules**, so Gitar now flags anyone removing or changing the pin, rather than the pin itself. `app-ci` passed, and it merged at 2:51 PM.
5. **Verified.** The new pods (`9e41aea`) logged no dependency-conflict warning. By 2:53 PM Dash0 had **43 server spans from `slidekick` in 5 minutes** (`/readyz`, `/healthz`), the app's first traces since #7.
6. **Why it matters.** Without traces there are no request, error or latency panels for the app; the browser-to-backend trace link from [#18](https://github.com/Naterd/hackathon-cnh/pull/18) has no backend half; and there's nowhere to put the lookup's confidence score.

**The lesson:** "don't install OpenTelemetry packages" isn't enough. A transitive dependency counts too. On every requirements change, run `uv pip compile app/requirements.txt | grep -i opentelemetry`.

### Radar: Kubernetes UI and AI SRE

- **How it's wired:** the Radar agent (1.20.2) runs in namespace `radar` on cluster `cnh` and reports to our own Radar Cloud organization. Its connection config is in Secret `radar/radar-cloud-config`. Don't touch it, and don't upgrade Radar.
- **Where to look:** [app.radarhq.io](https://app.radarhq.io), then switch to our organization and open the cluster for Resources, Timeline, Issues and Checks. Take the Checks before and after screenshots there.
- **From Claude Code:** run `claude mcp add --scope user radar-cloud --transport http https://api.radarhq.io/mcp`, then `/mcp` > radar-cloud > Authenticate, and pick **our** organization. The `ship`, `debug-failure` and `demo-story` skills in `.agents/skills/` use it.
- **For the judges:** at least one AI investigation on a real issue, and the day's story from Radar's timeline. The full record is in [`STORY.md`](STORY.md).

#### How Radar helped us solve a problem

1. **Radar flagged it.** At 12:32 MT, Radar's **Issues** showed our test ingest run, Job `ingest/ingest-extract-ci` (4 talks), as **Unschedulable**: "Insufficient cpu". Nothing had failed loudly. The Job looked like it was simply running.
2. **We asked Radar's AI SRE.** We started an AI investigation on the Job ([shared investigation](https://app.radarhq.io/c/k8l3A-86hTM/investigations/chat_nnIgqoMG-hY1WF54?org=org_LAtK4qe79DlO)). Its first answer was "healthy, no warning events". It had read the Job's events only.
3. **We steered it with evidence.** Radar's own pod events (`get_events`) showed what the Job view hid. We sent a follow-up asking it to check the pods. It **revised its finding** to a root cause with confidence 0.85: "A new spot node's boot, storage-driver registration, and image pulls delayed pod index 3 by about 95 seconds."
   - 48 s unschedulable: the one spot node fits only three 2-CPU pods, so the autoscaler added a node (1 → 2).
   - Then a `FailedMount` because the new node's GCS FUSE CSI driver wasn't registered yet.
   - Then three cold image pulls, including the 368 MB ingest image.
4. **What we decided.** The full V2 run (about 370 talks) pays that ~95 s once for each new spot node. We weighed pre-scaling the pool and decided **not to**: a minute and a half per node is small next to the run, and pre-scaling means changing and then undoing the node pool on a shared cluster. We checked Radar's second suggestion, GKE Image Streaming, and found it doesn't apply to us: it's off on the pool, and it only works for images in Artifact Registry, while ours come from GHCR.
5. **Checks, before and after.** Radar Checks reported 10 medium findings in our namespaces (see [`STORY.md`](STORY.md)). We fixed the ones that apply in a reviewed PR: a PodDisruptionBudget, no auto-mounted service-account token, and a soft topology spread for the app. `podHARisk` remains while the default pool has one node; the soft spread kicks in when a second node appears.

### Elasticsearch: document store and search

- **How it's wired:** an Elastic Cloud Serverless project, `slidekick` (vector-optimized, Elasticsearch 9.7.0). Everything lives in index **`kubecon-moments-v1`**, reached through alias **`kubecon-moments`**: one document per slide still, with talk metadata, timings, OCR text and captions.
  - `slide` (the image), `said` (captions) and `abstract` are `semantic` fields, embedded by Elastic's inference service with `.jina-embeddings-v5-omni-small`.
  - A lookup is one hybrid query: RRF over image kNN, OCR keywords and text-to-image.
- **Where to look:** [cloud.elastic.co](https://cloud.elastic.co) > Serverless projects > `slidekick` > open Kibana > Discover on `kubecon-moments`. Or use Dev Tools: `GET kubecon-moments/_count`, `GET kubecon-moments/_mapping`.
- **Access from a terminal or agent:** the URL and API key are in Secret `ingest/elastic` (keys `ELASTIC_URL` and `ELASTIC_ES_API_KEY`). **Both count as secrets.** Keep them out of git, chats and output.

  ```bash
  (
    umask 077
    kubectl -n ingest get secret elastic -o jsonpath='{.data.ELASTIC_ES_API_KEY}' | base64 -d | sed 's/^/Authorization: ApiKey /' > ~/.es-auth-header
    kubectl -n ingest get secret elastic -o jsonpath='{.data.ELASTIC_URL}' | base64 -d > ~/.es-url
  )
  curl -s -H @"$HOME/.es-auth-header" "$(cat ~/.es-url)/kubecon-moments/_count"
  ```
- **How the app queries it:** [`docs/search.md`](docs/search.md), with a runnable example in `docs/search_example.py`.
- **Count with `_count`, not `_cat/indices`.** `_cat`'s `docs.count` includes the nested chunks the `semantic` fields create, about 3 per still, so it shows 39,602 for our 12,516 documents.
- **Rules that bite:** exclude `slide` and `said` from `_source` in every search. Images must be at most 1 MB decoded. Filters go inside the `knn` clause. Bulk returns 200 even when items fail. See `AGENTS.md`.

## Demo-ready checklist

What's left before each sponsor tool counts as **integrated and ready to demo**. Every item maps to the track's judging criteria from the [hackathon page](https://www.wemakedevs.org/hackathons/cnh), or is marked *(bonus)*.

- **Tick a box only with evidence:** a PR, link or file. Tick it in the same PR that closes it.
- **A tool is ready** when every non-bonus box is ticked.
- **Owners:** "team" means nobody has picked it up yet. Claim it by putting your name there.

### Gitar: Never Merge Broken

Judged on: features landing through PRs Gitar reviewed; review instructions **and repository rules**; CI failure diagnosis **and auto-apply** on real problems.

- [x] The Gitar app reviews every PR ([#1](https://github.com/Naterd/hackathon-cnh/pull/1) to [#13](https://github.com/Naterd/hackathon-cnh/pull/13))
- [x] Review instructions in [`.gitar/review/main.md`](.gitar/review/main.md)
- [x] Real findings acted on, for example: [#4](https://github.com/Naterd/hackathon-cnh/pull/4) → [#5](https://github.com/Naterd/hackathon-cnh/pull/5) (duplicate Argo CD Application), [#19](https://github.com/Naterd/hackathon-cnh/pull/19) (a 300 KB PNG that broke `kubectl apply`), [#17](https://github.com/Naterd/hackathon-cnh/pull/17) (bulk indexing dropped throttled items)
- [x] PR CI for ingest: `ingest-ci` on our GKE runners ([#11](https://github.com/Naterd/hackathon-cnh/pull/11))
- [x] Repository rules in [`.gitar/rules/`](.gitar/rules/): `deploy-changes` and `index-mapping` ([#15](https://github.com/Naterd/hackathon-cnh/pull/15))
- [x] PR CI for the app: `app-ci` (smoke test plus image build) on GitHub-hosted runners ([#15](https://github.com/Naterd/hackathon-cnh/pull/15))
- [x] Auto-apply turned on: `gitar auto-apply:on` on [#16](https://github.com/Naterd/hackathon-cnh/pull/16), and Gitar's review reports "Auto-apply is on". Keep doing it on each feature PR.
- [x] **Gitar fixed real problems itself:** on [#16](https://github.com/Naterd/hackathon-cnh/pull/16) its review found that huge-pixel uploads could OOM the pod and it pushed the fix with auto-apply ([`a8db50f`](https://github.com/Naterd/hackathon-cnh/commit/a8db50f); we refined it with `search.BadPhoto`), and on [#3](https://github.com/Naterd/hackathon-cnh/pull/3) it narrowed its own placeholder exemption so deploy manifests still pointing at `team-__TEAM__` would be reviewed ([`b808686`](https://github.com/Naterd/hackathon-cnh/commit/b808686)). Gitar has also found 13 other problems that we fixed before merging, across #3, #8, #9, #11, #13, #16, #17 and #19.
- [ ] *(bonus)* Gitar diagnoses a real **CI failure**. No PR's CI has failed yet, so this hasn't come up. Don't break CI on purpose. Owner: team.

### Dash0: Stop Flying Blind

Judged on: depth of instrumentation (traces, logs, metrics, real-user monitoring); Agent0 or the MCP used to find and fix something real; observability that's part of the product. *(Bonus: a dashboard or SLO.)*

- [x] Operator on GKE; `slidekick` and `ingest` monitored; app traces flowing (see [Observability (Dash0)](#observability-dash0))
- [x] Metrics: Kubernetes CPU, memory and Jobs, plus request rate, errors and latency from spans
- [x] Logs: pod logs from both namespaces arrive in Dash0 (1:30 PM check: 4,437 records an hour from `slidekick`, 2,068 from `ingest`)
- [ ] Ingest telemetry: the V2 run fills the dashboard's "ingest: pipeline" section. Owner: whoever runs V2.
- [x] Real-user monitoring: Dash0's web SDK in `app/templates/base.html` as service `slidekick-web` ([#18](https://github.com/Naterd/hackathon-cnh/pull/18)). The ingest-only token comes from `app-credentials`. Verified 2:18 PM: a page load reached Dash0 as page views and web events in about 15 s. (Ad blockers can block it on a laptop; phones are fine.)
- [x] The match recorded on the lookup span ([#25](https://github.com/Naterd/hackathon-cnh/pull/25)): `slidekick.match.talk_id`, `.score`, `.gap` (lead over the runner-up, the confidence), `.found`, `.degraded` and `.fingerprint_cosine`, plus child spans `lookup.ocr` and `lookup.search`. Live by 3:29 PM, and already filterable in Dash0 for visitors' lookups
- [x] Observability in the product ([#25](https://github.com/Naterd/hackathon-cnh/pull/25)): the results page shows *Matched in 7.5 s · reading the slide 3.8 s, search 0.8 s · trace 82418e82*, with the full Dash0 trace ID on hover. `tests/observability_test.py` keeps it, and the `opentelemetry-api` pin, from drifting
- [x] **Agent0 found something real and offered a valid fix:** the Python app had sent no traces since #7, because FastAPI pulls in `opentelemetry-api` 1.45.2, which turned the Dash0 injector off. The fix is to pin `opentelemetry-api==1.44.0` ([Agent0 thread](https://app.dash0.com/agent0?org=2f80d046-5fdb-425e-bb24-be79c87eb43d&s=eJydj8sKwjAQRf9l1i32qaE7wY0rQf2BaXNbAzEtTbKQ0n83KFREF-JuuMPMOXeiFVUTKePQjewgtx2MSx6ZPQwwVLWsLSJylxEs95IqQi5SZPkmLrO1iIu0kLFo0zpmbkpkSESZCJojavvGW8izuuLIpgNVxmu95DsfiKo3bzvJji1coEi07LWj6MPupCRqHr9J3obwiUxvQHMwWPFS5zX90eR5_Ct3vgOyNW8n), [the story](#how-dash0-helped-us-solve-a-problem))
- [x] Tracing fix shipped by Agent0 in [#21](https://github.com/Naterd/hackathon-cnh/pull/21) (opened by Dash0's GitHub App) and verified: 43 `slidekick` server spans in 5 minutes after the deploy, the first since #7
- [x] *(bonus)* Dashboard *SlideKick: Four Golden Signals*: app, ingest Jobs, ingest pipeline
- [x] *(bonus)* SLO `slidekick-availability`: 99% of synthetic `/healthz` checks pass, over a rolling 28 days. A latency SLO on `/api/lookup` can follow once the lookup is real.
- [x] *(bonus)* Synthetic check `slidekick-healthz`: GET `https://slidekick.us/healthz` every minute from Oregon and N. Virginia. It asserts status 200, body `ok`, and a TLS certificate valid for more than 7 days; it's degraded above 1 s. It sends tracing headers, so each probe also shows up as a trace in our app.

### Radar: AI SRE

Judged on: running on Kubernetes with Radar connected; Radar's UI and MCP used throughout building and operating; an AI investigation of a real issue.

- [x] Cluster `cnh` connected to our Radar organization `hackathon`
- [x] Radar's MCP in use: Checks, Issues, events, investigations
- [x] AI investigation of a real issue, shared and corrected by a follow-up ([story](#sponsor-tools), [`STORY.md`](STORY.md))
- [x] Checks "before" recorded in [`STORY.md`](STORY.md)
- [x] Checks "after": `slidekick` went from 6 to 3 findings after [#13](https://github.com/Naterd/hackathon-cnh/pull/13) ([`STORY.md`](STORY.md#after))
- [x] One deploy verified **through Radar's MCP**: Argo CD's sync state, the rollout with `wait_for`, and the live version ([`STORY.md`](STORY.md#after)). Radar can't trigger a sync, because our Radar user lacks `patch` on Argo CD Applications.
- [x] A decision Radar informed: each new spot node costs about 95 s, and we **accept that cold start for V2** instead of pre-scaling the pool. It's small next to the run time, and it avoids changing and undoing the node pool ([`STORY.md`](STORY.md#follow-up-steering-the-investigation-1242-mt))
- [ ] *(bonus)* Fix the high Checks finding on the ARC controller (`arc-systems`). Owner: Nate.
- [ ] Draft the 5:30 story from Radar's timeline with the `demo-story` skill, at about 5:00. Owner: Joel.

### Elasticsearch: Find the Right Talk

Judged on: Elasticsearch as the core data layer; smart search (keyword, semantic, vector or hybrid).

- [x] Serverless project `slidekick`; alias `kubecon-moments` → `kubecon-moments-v1`, 12,516 documents (one per slide still, all 370 talks); semantic `slide`, `said` and `abstract` fields with Jina
- [x] Hybrid RRF lookup documented and runnable: [`docs/search.md`](docs/search.md), `docs/search_example.py`
- [x] Accuracy measured with `ingest/eval.py`; results in [`docs/results.md`](docs/results.md)
- [x] **`/api/lookup` runs the real hybrid query** ([#16](https://github.com/Naterd/hackathon-cnh/pull/16), [#19](https://github.com/Naterd/hackathon-cnh/pull/19)). Live test at 2:56 PM: `docs/test-photos/appset-603s.jpg` returned *Mastering ApplicationSet: Advanced Argo CD Automation* as the top talk, with moments at 549 s, 269 s and 615 s
- [x] Results come back as real talks and `youtu.be/…?t=` moment links from Elasticsearch. Dash0 traced the lookup at 6.8 s, of which about 1.6 s is the three Elasticsearch calls; the rest is OCR and image work in the pod.
- [ ] *(bonus)* This week's schedule and decks indexed for live mode. Owner: team.

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
- **Add our own signals** with the `opentelemetry` API, imported in a `try/except ImportError` as in `ingest/telemetry.py`. Don't install any `opentelemetry-*` package: the operator turns itself off when it finds one. The one exception is `opentelemetry-api`, which fastapi requires; it's pinned in `app/requirements.txt` to the exact version the operator's SDK needs (1.44.0), and any other version turns tracing off. Put the match confidence on the lookup span, and count ingest progress: talks indexed, stills per second, Elasticsearch HTTP 400s.
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
(
  umask 077
  kubectl -n dash0-system get secret dash0-authorization-secret -o jsonpath='{.data.token}' | base64 -d | sed 's/^/Authorization: Bearer /' > ~/.dash0-auth-header
)
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
  - **Don't re-run an old `build-and-deploy` run once `main` has moved on.** The Action writes its own image tag onto the *latest* `main`, so re-running an old run points the site at older code. Re-run only the newest run.
  - **Deploy history:** builds are under [Actions > build-and-deploy](https://github.com/Naterd/hackathon-cnh/actions/workflows/deploy.yaml) (filter with `is:failure` or `is:success`). Every tag change is in the [history of `deploy/kustomization.yaml`](https://github.com/Naterd/hackathon-cnh/commits/main/deploy/kustomization.yaml). What Argo CD actually synced is in Radar Cloud > the cluster > GitOps, or `kubectl -n argocd get application slidekick -o jsonpath='{.status.history}'`.
  - **Don't `kubectl edit` what Argo CD owns** (Deployment and Service `app`, BackendConfig `app`). Self-heal puts the git version back, so change those in git.
  - The Service is in sync wave 1, so traffic switches only after the new Deployment is healthy. The load balancer's health check is `/readyz` (`deploy/backendconfig.yaml`).
  - **Ingest Jobs aren't deployed by Argo CD.** Run them with `kubectl apply`, because Argo CD's prune and self-heal don't suit Jobs that run to completion.
- **Secrets:** never put them in git, the image or a build argument. They live in Kubernetes Secrets: `app-credentials` for the app, and `ingest/elastic` (Elasticsearch URL and API key) for the ingest Jobs.
- **The app contract:** a Dockerfile at the repo root; the app listens on 8080, serves `/healthz` and `/readyz`, and runs as user 1000. In the cluster it runs in namespace `slidekick` behind Service `app`, port 80.
- **Run the app locally:** in `app/`, `pip install -r requirements.txt`, then `uvicorn main:app --reload --port 8080`. Use `--reload` only on your laptop.
- **Radar:** the cluster is in our own Radar Cloud organization, not the hackathon's. Connect Claude Code with `claude mcp add --scope user radar-cloud --transport http https://api.radarhq.io/mcp`, then pick our organization when you authenticate. `AGENTS.md` and `.agents/skills/` started from the [Radar starter](https://github.com/skyhook-io/cnh-starter) and now describe our cluster.

## Layout

| Path | What |
| --- | --- |
| `app/` | The FastAPI app: `main.py`, Jinja templates in `templates/`, plain JavaScript and CSS in `static/` |
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
