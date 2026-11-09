# SlideKick: the day in Radar

Cluster `cnh` (Radar id `k8l3A-86hTM`) in our Radar Cloud organization `hackathon`. Namespaces: `slidekick` (app) and `ingest` (Jobs).

## Before

Nov 9, 12:24 MT (19:24 UTC). Radar Checks (`get_cluster_audit`) for `slidekick` and `ingest`: 10 findings, all medium, no high or critical.

Checks: https://app.radarhq.io/checks?scope=k8l3A-86hTM

**slidekick (6)**
- Security, `automountServiceAccountToken`: Deployment `app`
- Security, `readOnlyRootFs`: Deployment `app`, container `app`
- Reliability, `missingPDB`: Deployment `app` (2 replicas, no PodDisruptionBudget)
- Reliability, `missingTopologySpread`: Deployment `app`
- Reliability, `podHARisk`: Deployment `app` (both pods on `gke-cnh-default-pool-2a6e0144-mzw8`)
- Efficiency, `orphanConfigMapSecret`: Secret `app-credentials` (not referenced yet)

**ingest (4)**
- Efficiency, `orphanConfigMapSecret`: ConfigMaps `ingest-code`, `ingest-talks`, `yt-download`; Secret `elastic`. These are expected between runs, because only the ingest Jobs reference them.

## Investigation

Nov 9, 12:32 MT: a Radar Issue flagged Job `ingest/ingest-extract-ci` (a 4-talk Indexed Job run) as **Unschedulable**: "Insufficient cpu, node affinity/selector mismatch". At 12:35 MT we started a Radar AI investigation on the Job: https://app.radarhq.io/c/k8l3A-86hTM/investigations/chat_nnIgqoMG-hY1WF54?org=org_LAtK4qe79DlO

**The investigation's verdict:** healthy, certainty "established", confidence 0.9. 3 of 4 indexes were done, the 4th was running, and there were "no warning events". The Job did complete, 4 of 4 at 12:37:12 MT.

**What the pod's events in Radar showed:** the verdict missed the pod-level warnings, because it read the Job's events only. Timeline for pod `ingest-extract-ci-3-d6m96`:
- 19:32:33 UTC `FailedScheduling`: Insufficient cpu. The existing spot node fits 3 pods at 2 CPU each.
- 19:32:34 `TriggeredScaleUp`: the spot pool grows from 1 to 2 nodes.
- 19:33:21 `Scheduled` on the new node, 48 s after the pod was created.
- 19:33:22 `FailedMount` ×2: the GCS FUSE CSI driver wasn't registered yet on the new node.
- 19:34:08 the container starts, after a 16 s pull of the 368 MB ingest image.

**What we learned:** each new spot node costs about 95 s before a pod on it runs: scale-up, then CSI driver registration, then image pulls. At 2 CPU per pod, an e2-standard-8 fits 3 pods. **Decision for the V2 run:** accept the cold start; no pre-scaling (see the follow-up below).

### Follow-up: steering the investigation (12:42 MT)

We asked it to check the pods' events, not only the Job's. It revised its finding: "A new spot node's boot, storage-driver registration, and image pulls delayed pod index 3 by about 95 seconds", certainty "likely", confidence 0.85. That matches the pod events above. Its fix was to pre-scale the ingest pool and turn on GKE Image Streaming. **We checked:** Image Streaming is off on the `ingest` pool, and it only works for Artifact Registry images, while ours come from GHCR. We also weighed pre-scaling the spot pool, and **decided to accept the ~95 s cold start per new node**: it's small next to the run, and pre-scaling means changing and then undoing the node pool on a shared cluster.

## After

PR [#13](https://github.com/Naterd/hackathon-cnh/pull/13) "Fix Radar Checks findings for the app": a PodDisruptionBudget (`minAvailable: 1`), `automountServiceAccountToken: false`, and a soft `topologySpreadConstraints` for Deployment `app`.

**Shipped and verified through Radar's MCP** (Nov 9, 1:22 MT): `fleet_list_gitops` showed Argo CD auto-syncing `291e088` (`deploy: 333fd51`). `wait_for` showed Deployment `app` at generation 8, rolled out 2/2. https://slidekick.us returns `version 333fd51`. (Triggering the sync through Radar's `manage_gitops` was refused, because our Radar user can't `patch` Argo CD Applications, so Argo CD's own auto-sync did it.)

**Checks after** (`get_cluster_audit`, namespace `slidekick`): **6 → 3 findings.**
- Fixed: `missingPDB`, `missingTopologySpread`, `automountServiceAccountToken`.
- Still open, as expected: `readOnlyRootFs` (needs testing with the Dash0 injection), `podHARisk` (the default pool has one node; the soft spread takes effect when a second one appears), `orphanConfigMapSecret` on `app-credentials` (the app doesn't read it yet).

## The story

Nov 9, 2026 · all times Mountain.

- **What we built:** [SlideKick](https://slidekick.us). Point a phone at a KubeCon slide, or at a laptop playing the talk, and get the talk, the speaker and a YouTube link at the second that slide was on screen; a camera roll becomes a timeline of your day. It's for attendees who missed a slide, a room or a talk. After [#29](https://github.com/Naterd/hackathon-cnh/pull/29) it also works with a screen reader: the slide is read out in its own words, along with what the speaker said at that moment.
- **How we shipped:** 27 PRs and 61 commits since midnight, every PR reviewed by Gitar, and 25 releases, each built by GitHub Actions and synced to GKE by Argo CD, with every rollout on Radar's timeline. A merge is live in about 2.5 minutes ([#5](https://github.com/Naterd/hackathon-cnh/pull/5)). Gitar caught 15 real problems before merge and pushed 2 fixes itself, including the one that stopped a crafted upload from OOM-killing the pod ([#16](https://github.com/Naterd/hackathon-cnh/pull/16), `a8db50f`).
- **What broke, and how we saw it:**
  - Dash0 showed **zero traces** from the app at 2:40 PM, though it was serving traffic. The Dash0 injector had been logging why at every pod start: FastAPI pulls in `opentelemetry-api` 1.45.2, and the injector turns itself off unless it finds 1.44.0. **Agent0 opened the fix PR itself** ([#21](https://github.com/Naterd/hackathon-cnh/pull/21)), and traces were back at 2:53 PM.
  - From 3:03 to 3:25 PM, three deploys failed on a GHCR permission change. The build logs showed it, and the package settings were fixed.
- **The investigations:** at 12:32 PM Radar flagged a 4-talk ingest Job as Unschedulable. Its AI investigation first said "healthy", because it had read only the Job's events. We pointed it at the pod's events, and it revised to a root cause: each new spot node costs about 95 s (scale-up, CSI driver registration, cold image pulls). We decided to accept that cost for ingest rather than pre-scale ([investigation](https://app.radarhq.io/c/k8l3A-86hTM/investigations/chat_nnIgqoMG-hY1WF54?org=org_LAtK4qe79DlO), [above](#investigation)).
- **What Elasticsearch does in one lookup:** one hybrid RRF query over 12,516 slide stills from 370 talks (image kNN with Jina embeddings, OCR keywords, text-to-image) plus a pixel-fingerprint leg, grouped by talk in code. In the last 2 hours, 36 real lookups, every one HTTP 200. Most were photos of a recording on a laptop or projector (25 `found=recording`). The median was 7.7 s, and the results page shows the split: *Matched in 7.5 s · reading the slide 3.8 s, search 0.8 s* ([#25](https://github.com/Naterd/hackathon-cnh/pull/25)).
- **Checks, before and after:** Radar Checks for `slidekick` went from **6 findings to 1**.
  - Fixed: the PodDisruptionBudget, topology spread and service-account token ([#13](https://github.com/Naterd/hackathon-cnh/pull/13)).
  - `app-credentials` is now referenced ([#16](https://github.com/Naterd/hackathon-cnh/pull/16)), and the pods now span two nodes.
  - Left: `readOnlyRootFs`, which needs testing against the Dash0 injector.
  - `ingest`'s findings are ConfigMaps that only exist between Job runs.
  - Meanwhile the synthetic check on `/healthz` has passed **308 of 308** runs, so the availability SLO is at 100%.

### Screenshots

- Radar timeline: https://app.radarhq.io/c/k8l3A-86hTM/timeline
- The Argo CD Application in Radar: https://app.radarhq.io/c/k8l3A-86hTM/resources/applications?resource=argocd/slidekick&apiGroup=argoproj.io
- Radar Checks for `slidekick`, next to the morning's: https://app.radarhq.io/checks?scope=k8l3A-86hTM&namespace=slidekick
- The Radar investigation, with the follow-up: https://app.radarhq.io/c/k8l3A-86hTM/investigations/chat_nnIgqoMG-hY1WF54?org=org_LAtK4qe79DlO
- The Agent0 thread and its PR [#21](https://github.com/Naterd/hackathon-cnh/pull/21)
- Dash0's *SlideKick: Four Golden Signals* dashboard, plus the `slidekick-healthz` synthetic check and the `slidekick-availability` SLO
- Gitar's review on [#16](https://github.com/Naterd/hackathon-cnh/pull/16) (the upload OOM, with its fix commit) and on [#4](https://github.com/Naterd/hackathon-cnh/pull/4) (the duplicate Argo CD Application)
- A lookup on a phone: the photo in, the talk, the timing line and "Jump to 0:05" out
