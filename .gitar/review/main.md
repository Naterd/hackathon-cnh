# SlideKick review instructions

SlideKick is a one-day hackathon project by a team of three. A phone photo of a KubeCon slide returns the talk and a YouTube link at the moment that slide appeared. The stack is FastAPI with a plain-JavaScript PWA, Elasticsearch Serverless and GKE with Argo CD. Most PRs are written with Claude Code. The demo is at 5:30 PM on the day, so a review should find what would break the deploy, leak a secret or wreck the demo, and skip everything else.

## How to review

- Open with a one-line verdict: **safe to merge**, or **blocking: N** followed by the blocking findings.
- Report at most 8 findings, most severe first. Give each one a concrete fix: a diff, or the exact line to change.
- Check that the diff does what the PR description says. Flag claims like "tested" or "verified on the cluster" when the diff doesn't support them.
- When unsure whether something is a bug, say so and give the reason rather than guessing.

## Blocking

Report these as blocking review findings.

1. **Secrets.** The repo and the GHCR image are public. Any credential in code, manifests, the Dockerfile, a build argument, a workflow, a test fixture or a log line is blocking: Elasticsearch URL and API key, the Dash0 token, a kubeconfig, a GCP key, a GitHub token. Secrets come from the Kubernetes Secrets `app-credentials` (app) and `ingest/elastic` (ingest). Printing or logging a secret's value is also blocking.
2. **The app contract.** The container listens on 8080, serves `/healthz` and `/readyz`, and runs as a non-root user (uid 1000). Service `app` stays on port 80. Block changes that break any of these, or that loosen the pod's `securityContext` (root, privilege escalation, added capabilities).
3. **The deploy bot's lines.** The GitHub Action owns `images[].newName` and `newTag` in `deploy/kustomization.yaml`. Block a hand edit to them, and any workflow change that could make the deploy commit loop (a push that triggers its own build) or drop `[skip ci]`.
4. **Elasticsearch: images in responses.** `slide` (a base64 image) and `said` (captions) are `semantic` fields. Every search must exclude both from `_source`, and nothing may highlight `slide`. Otherwise each hit carries a megabyte of base64.
5. **Elasticsearch: the semantic field's limits.**
   - Each image sent to `slide` must be a base64 data URL of at most 1 MB of decoded bytes, one image per value. Code that indexes or queries an image without resizing it first is blocking.
   - Filters on a kNN search go inside the `knn` clause; a filter outside it doesn't pre-filter.
   - Aggregations, sorting and ES|QL don't work on the semantic field. Group results by talk in the API.
6. **Ingest Jobs.**
   - A Job on the spot pool needs `nodeSelector: {workload: ingest}`, the toleration for `workload=ingest:NoSchedule`, and CPU and memory requests and limits.
   - Spot nodes get preempted, so a talk must be safe to re-run: skip finished work, and overwrite documents by stable id.
   - Don't call YouTube for captions or video again. It rate-limits within hours (HTTP 429). New talks get Whisper.
7. **The image is first built after the merge.** Nobody builds images locally, and CI builds the app image only on `main`, so this review is the last check before a broken pod. Block:
   - A new third-party import in `app/` without a pinned entry in `app/requirements.txt`, or in `ingest/` without one in `ingest/requirements.txt`. It works on a laptop that already has the package and fails in the pod.
   - A file the app reads at runtime that the Dockerfile doesn't copy, or that `.dockerignore` matches. The Dockerfile copies `app/`, plus `docs/search_example.py` and `docs/layout-kubecon-na-2024.png`, which the lookup imports and reads.
   - Dropping `ARG APP_VERSION` / `ENV APP_VERSION` from the Dockerfile. The footer prints it, and the `ship` skill checks it to prove a deploy.
   - A Dockerfile `CMD` with `--reload` or `--workers`. It must stay one uvicorn process.
   - A new module under `ingest/` that isn't added to the `--from-file` list in `ship_code` in `ingest/run.sh`. Jobs run the ConfigMap copy in `/code`, so the import fails on the cluster.
8. **Licensing.** Link to YouTube and to the speakers' deck PDFs. Block code that stores or serves the talk videos or the deck PDFs from our own app or bucket for users to download.

## Worth a comment, not blocking

- **Dash0 labels.** New workloads need `app.kubernetes.io/name` and `app.kubernetes.io/version`; ingest Jobs use `app.kubernetes.io/name: slidekick-ingest`. Dash0's operator injects OpenTelemetry automatically and turns itself off if the image already has any `opentelemetry-*` package, so flag one in requirements or the Dockerfile (except the `opentelemetry-api==1.44.0` pin in `app/requirements.txt`, which fastapi requires and the operator needs at exactly that version; flag removing or changing it), a hand-rolled SDK setup, or an OTLP protocol other than `http/protobuf`.
- **Elasticsearch HTTP 400s and 429s.** Count them, and retry throttled bulk items rather than dropping them.
- **Accessibility** (the demo uses VoiceOver): every control has a label, results are announced through an `aria-live` region, and images have alt text.
- **Camera roll.** Read EXIF `DateTimeOriginal` before drawing the photo to a canvas, because the canvas drops it. A time without an offset is Mountain Time. Don't rely on GPS.
- **Content Security Policy.** Dash0's browser SDK needs `https://ingress.us-west-2.aws.dash0.com` in `connect-src`.
- **Frontend dependencies.** The plan is plain JavaScript with no build step. Question a PR that adds React, a bundler or a second toolchain.
- **Disclosure.** The README's "AI assistance" section is required by the hackathon rules. Flag a PR that removes it.

## Don't report

- Style, naming, formatting, docstrings, type hints or missing tests, unless the gap hides a real bug.
- Refactors or abstractions "for later". This code lives for a day.
- Placeholders (`__TEAM__`, `OWNER/REPO`) in docs and anything under `examples/`. In `deploy/` and `argocd/` they are a bug: the app must deploy to `slidekick`, ingest Jobs to `ingest`, and nothing may reference the hackathon's `cnh` gateway.
- Project ids, bucket names, the domain or the static IP in docs and scripts. They're public on purpose and aren't secrets.
- Findings on the pre-installed Argo CD, Argo Rollouts or Dash0 operator.
- Unpinned GitHub Action tags such as `@v5`.
