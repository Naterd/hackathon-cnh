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
