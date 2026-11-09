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
| App, ingest Jobs, Radar, Dash0, Gitar | To do |

## How it works

1. **Ingest (GKE spot Jobs).** Crop each recording to its slide area, detect slide changes, drop near-duplicates, OCR each still, and attach the captions spoken while it was on screen. One indexer loads the results into Elasticsearch.
2. **Lookup.** Flatten the phone photo, OCR it, and run one hybrid search. RRF merges image kNN on the multimodal `semantic` field, OCR keyword matching, and text-to-image matching. Results are grouped by talk and returned with a YouTube link at the right second.
3. **Camera roll.** Read each photo's EXIF time in the browser, match every photo, and place it on a timeline. For this week's photos, the session running at that time ranks higher.

Stack: FastAPI with server-rendered pages, a PWA front end in plain JavaScript, Elasticsearch Serverless with Elastic Inference Service, and GKE with Argo CD.

## Working in this repo

- **Pull before every commit:** `git pull --rebase --autostash`. Enable the hook that enforces it once per clone: `git config core.hooksPath .githooks`.
- **Deploying:** push to `main`. The GitHub Action builds the image to GHCR and commits a `deploy: <sha>` tag change, and Argo CD on GKE syncs it. The repo and the image must stay public, because the cluster pulls without credentials.
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
4. **Deploy:** `kubectl apply -f argocd/application-byo.yaml`. From then on, every push to `main` deploys.

## Data and credits

- Talk recordings: CNCF's KubeCon + CloudNativeCon North America 2024 playlist on YouTube, licensed CC BY. We link to YouTube rather than re-hosting video.
- This week's schedule and decks come from the official KubeCon NA 2026 schedule. Decks are linked, not re-hosted.
- Deploy scaffolding: [skyhook-io/cnh-starter](https://github.com/skyhook-io/cnh-starter).

## AI assistance

We built this with AI coding assistants (Claude Code), as the hackathon rules allow and require us to disclose. The team reviewed and understands the code we submit.
