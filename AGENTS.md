# Notes for your coding agent

SlideKick: photograph a KubeCon slide and get the talk, the speaker and a YouTube link at the moment that slide appeared. FastAPI with server-rendered pages and a plain-JavaScript PWA, Elasticsearch Serverless, and GKE with Argo CD. The README is the short version: what the app does, the big wins and how it works. `docs/details.md` has the status, the sponsor-tool setup and stories, observability and setup. The repo and the image are public.

## Team rules

**Pull before every commit.** Three of us and the deploy bot push to `main` all day. Before each `git commit`, run `git pull --rebase --autostash` as its own command. The pre-commit hook in `.githooks/` refuses a commit while the branch is behind `origin`. Enable it once per clone with `git config core.hooksPath .githooks`.

**Large changes go through a pull request.** The PR is what triggers the Gitar review, and the Gitar track judges reviewed PRs landing all day.
- **Large:** a new feature, app or ingest code, Kubernetes or Argo CD manifests, the index mapping, CI, or more than about 50 changed lines.
- **Small:** typos, docs and one-line config fixes, which can go straight to `main`.
- **Steps for a large change:**
  1. Branch from an up-to-date `main`: `git switch -c <short-name>`.
  2. Commit and push the branch.
  3. Open the PR with `gh pr create --fill`.
  4. Wait for the review and fix what it finds.
  5. Merge with `gh pr merge --squash --delete-branch`. The merge deploys.

**Don't build or pull images on your laptop.** Pulls over the venue wifi are slow, and the fast loops below don't need an image.
- **App:** in `app/`, run `uvicorn main:app --reload --port 8080` (any free port). Reload on save is the inner loop.
- **Ingest:** `ingest/run.sh` ships your checkout's code to the cluster as a ConfigMap, so a code change runs on GKE without an image build. Only a change to `ingest/Dockerfile` or `ingest/requirements.txt` needs a new image. Push the branch, and `ingest-image` builds `ghcr.io/naterd/hackathon-cnh/ingest:<sha>`. Run that tag with `INGEST_IMAGE=ghcr.io/naterd/hackathon-cnh/ingest:<sha> ingest/run.sh ...`. `:latest` changes only on `main`.
- **Images:** GitHub Actions builds them. The app image is first built when the PR merges, so Gitar's review is where a Dockerfile or dependency mistake gets caught. Check your change against `.gitar/review/main.md` before opening the PR.

## Writing code

- Write the least code that does the job. No speculative helpers, options, config or abstractions.
- Comment only a non-obvious why. Don't restate the code, add banner comments, or docstring a trivial function.
- Commit messages and PR descriptions: a short title, then a few lines on what changed and why. No filler, and no list of every file touched.
- Plain JavaScript in the browser: no framework, bundler or build step.
- `.gitar/review/main.md` lists what blocks a merge. Gitar checks every PR against it, so check your change against it first.
- Run shell commands one at a time, in plain form. Claude Code asks for approval on any command with `$(…)`, `$?` or shell variables. Write and edit files with your file tools, not heredocs.

## Where it runs

GKE cluster `cnh`, project `five9-cyclops-playground`, zone `us-west3-a`.

- **App:** namespace `slidekick`, Deployment and Service `app`, behind the GKE Ingress at https://slidekick.us. The load balancer health-checks `/readyz`.
- **Ingest:** Jobs in namespace `ingest` on the spot pool, with `nodeSelector: {workload: ingest}` and a toleration for `workload=ingest:NoSchedule`. Run them with `kubectl apply`: Argo CD doesn't manage Jobs.
- **Deploys:** a merge to `main` runs the `build-and-deploy` Action, which pushes the image to GHCR and commits `deploy: <sha> [skip ci]`. Argo CD (Application `slidekick` in namespace `argocd`, no web UI) syncs `deploy/` within about 3 minutes. Pull that deploy commit before your next push. Markdown-only pushes skip the build.
- **Rollback:** `git revert` the bad commit on `main`. Don't `kubectl edit` what Argo CD owns: self-heal puts the git version back.
- **Secrets:** `slidekick/app-credentials` for the app, `ingest/elastic` (`ELASTIC_URL`, `ELASTIC_ES_API_KEY`) for ingest. Never in git, the image, a build argument or your output.

## Radar (AI SRE track)

Judged: AI investigations, Radar's MCP server for shipping, syncing Argo CD and checking rollouts, and Radar's Issues, timeline and Checks. Use Radar's tools rather than kubectl to observe and operate the cluster.

- Radar runs in namespace `radar` and reports to our own Radar Cloud organization. Connect with `claude mcp add --scope user radar-cloud --transport http https://api.radarhq.io/mcp` and pick our organization when signing in.
- Call `list_clusters` once per session and pass our cluster's `id` as `cluster` on every call. Read each tool's schema; don't invent parameters.
- **After a merge:** the `ship` skill. Sync through Radar, wait for the rollout, and check that https://slidekick.us shows the new version. A healthy old revision isn't proof.
- **When something fails:** the `debug-failure` skill. Read the evidence first, name the cause, then fix it in git.
- **A symptom you can't explain:** suggest a Radar AI investigation. Start one only when the person asks.
- **Before the demo:** review Checks (`get_cluster_audit`) for `slidekick` and `ingest`. Fix what applies to our workloads, or say why it doesn't.

## Dash0 (observability track)

Judged: Agent0 and the Dash0 MCP server. Bonus: the Dash0 CLI, dashboards, alerts and synthetic monitoring.

- The Dash0 operator injects OpenTelemetry into Python pods in `slidekick` and `ingest`, and turns itself off if the image already has any `opentelemetry-*` package. Don't add one. The exception is `opentelemetry-api`, which fastapi requires: the operator allows it, but only at the exact version its SDK pins (1.44.0), so keep the pin in `app/requirements.txt` and check the operator's startup warning in the pod logs after bumping fastapi. Custom spans and metrics import `opentelemetry` in a `try/except ImportError`, like `ingest/telemetry.py`.
- Run one uvicorn process per pod and scale with replicas: auto-instrumentation doesn't support uvicorn's `--workers` or `--reload`.
- Label pod templates with `app.kubernetes.io/name` and `app.kubernetes.io/version`. Ingest Jobs use `app.kubernetes.io/name: slidekick-ingest` and `ingest/telemetry.py`.
- Query Dash0 through its MCP server (https://dash0.com/docs/dash0/ai/mcp) when connected, otherwise the API with the header file from `docs/details.md` (Observability (Dash0)). Never print the token.
- Dashboard: *SlideKick: Four Golden Signals* (`slidekick-golden-signals`). For a real issue, ask Agent0 for the root cause and have it open the fix as a PR, which Gitar then reviews like any other.

## Gitar (Never Merge Broken track)

Judged: Gitar's PR reviews, review instructions in `.gitar/review/`, repository rules, and auto-apply, which diagnoses failing CI and pushes fixes.

- Wait for Gitar's review on every PR. Fix blocking findings with new commits on the branch, and reply with the reason on any finding you won't fix.
- Gitar can push commits to your PR branch. Run `git pull --rebase` on the branch before adding more.
- Gitar reads this file and `.gitar/review/`. When a review misses a project gotcha, add it to `.gitar/review/main.md` in the same PR.
- Automations on PR events (labels, reviewers, checks) go in `.gitar/rules/<name>.md`: YAML frontmatter with `title`, `when` and `actions`, then a plain-language body.
- CI only builds on `main` today, so PRs have no checks for auto-apply to fix. A PR workflow that runs tests turns it on.

## Elasticsearch (Find the Right Talk track)

Judged: storing data, Jina embeddings through Elastic's inference service, keyword, vector, time-series and geo queries, aggregations and dashboards, and Elastic Workflows or Agent Builder, including MCP.

- **Before writing any search code, read `docs/search.md`.** It covers the index (alias `kubecon-moments`, 12,146 slide stills and 370 talks), every field, the lookup query and the gotchas we hit. `docs/search_example.py` is working reference code, and `docs/test-photos/` has photos with known answers. The app reads `ELASTIC_URL` and `ELASTIC_ES_API_KEY` from Secret `slidekick/app-credentials`.
- Serverless project `slidekick` (vector-optimized, 9.7). Embeddings come from Elastic's inference service (`.jina-embeddings-v5-omni-small`); don't add another embedding model.
- `slide` (a base64 image) and `said` (captions) are `semantic` fields. Exclude both from `_source` in every search, and never highlight `slide`. Each image is a data URL of at most 1 MB decoded, so resize first.
- A lookup is one hybrid query: RRF over image kNN on `slide`, OCR keyword matching and text-to-image matching. Set `rank_window_size` to at least `size`. Leave the lookup's kNN leg unfiltered: a filter there made it 3–6× slower. If another query does need a filter on kNN, it goes inside the `knn` clause. Aggregations, sorting and ES|QL don't work on semantic fields, so group results by talk in the API.
- Index mapping changes go through a PR. Bulk returns HTTP 200 even when items fail: check each item, and retry 429s.
- Elastic's agent skills: https://github.com/elastic/agent-skills.

## Skills

`.agents/skills/` has five procedures. Claude Code and Codex load them as skills; with another agent, read the matching `SKILL.md` first.
- `get-started`: connect a teammate's tools, and what's left for the day.
- `ship`: after a merge, prove the new version is live.
- `debug-failure`: from a symptom to the root cause and a fix in git.
- `canary-release`: ship as an Argo Rollouts canary, then promote or abort.
- `demo-story`: the 5:30 story across all four tracks.

## Never

- Apply a second Argo CD Application for `deploy/`. Two would fight over the same resources.
- Run `scripts/setup.sh`. It's for the hackathon's shared clusters.
- Touch another GCP project, cluster or repo.
- Generate load you can't bound. The spot pool scales to 256 vCPUs, so size ingest Jobs deliberately. Don't fetch from YouTube again; it rate-limits within hours.
- Store or serve the talk videos or deck PDFs ourselves. Link to them.
- Remove or reconfigure the pre-installed Argo CD, Argo Rollouts, Dash0 operator or Radar.
- Poll forever. After about 5 minutes, report what's blocking.
