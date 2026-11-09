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
