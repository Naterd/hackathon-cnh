---
name: debug-failure
description: Find the root cause of a SlideKick failure and fix it in git. Use when slidekick.us is down, slow, erroring or not updating, a pod is crash-looping or Pending, an Argo CD sync failed, or an ingest Job failed.
---

# Debug a failure, fix it in git

Work from evidence: read what Radar and Dash0 show, name the cause, then change the repo. Argo CD reverts direct edits to what it owns, so those fixes ship as commits.

Every Radar call takes `cluster` (our cluster's `id` from `list_clusters`) and a namespace: `slidekick` for the app, `ingest` for Jobs, `argocd` for the Application.

## 1. Stop the damage first

- **Canary failing** (`deploy/` has `rollout.yaml`): `manage_rollout` with `action: abort`. A canary paused at its step isn't broken. That's the person's release decision: the `canary-release` skill.
- **Deployment broken after a merge:** `git revert <bad commit>` on `main`, then the `ship` skill. Don't use `manage_gitops` rollback: auto-sync puts `main` back.

Tell the person what you did before going deeper.

## 2. Gather evidence

1. `issues` for the namespace: what's broken now, with a reason.
2. `get_changes` for the namespace, `since: 1h`: what changed just before it broke.
3. `diagnose` on the failing workload (`kind: deployment`, `name: app`). For a sync problem, diagnose the Application instead (`kind: application`, `group: argoproj.io`, `namespace: argocd`, `name: slidekick`). It bundles logs, events and startup blockers.
4. For more: `get_pod_logs` with `previous: true` for a crash, `get_events` for scheduling, and `get_resource` on the Application (`status.conditions`, `status.operationState`) for sync errors.
5. For errors or latency in a running app, check Dash0: the *SlideKick: Four Golden Signals* dashboard, then the failing spans and their logs.

## 3. Common causes here

| Symptom | Usual cause | Fix |
| --- | --- | --- |
| `ImagePullBackOff`, 401/403 | The GHCR package is private | The person makes it public: repo > Packages > Package settings > Change visibility |
| `ImagePullBackOff`, not found | The tag wasn't built | `gh run view <id> --log-failed`, fix the build |
| `CreateContainerConfigError` | A referenced Secret or key is missing (`app-credentials` exists only once someone creates it) | The person creates it with kubectl or Radar's resource editor. Values never go in git or your output. Fix a wrong reference in git |
| `OOMKilled`, exit 137 | Over `limits.memory` in `deploy/deployment.yaml` | Fix the memory use, or raise the limit |
| `CrashLoopBackOff` | The app exits at startup | `get_pod_logs` with `previous: true` |
| Ready 0/N | The app isn't on 8080, or `/readyz` isn't 200 | Fix the app, or the probe paths in `deploy/` |
| App pod `Pending`, `FailedScheduling` | The app pool (up to 6 nodes) is full, or requests are too big | Lower `replicas` or `requests` |
| Ingest pod `Pending` | Missing `nodeSelector: {workload: ingest}` or the `workload=ingest:NoSchedule` toleration, or no spot capacity | Fix the Job. Spot capacity comes back on its own |
| Ingest Job failing | Elasticsearch rejects bulk items (400) or throttles (429) | Read the per-item errors, and retry 429s. Never fetch from YouTube again |
| slidekick.us 502, pods healthy | The load balancer's health check (`/readyz`, BackendConfig `app`) fails, or the NEG has no ready endpoints | `diagnose` Service `app` with `probe: true`, and read the `ingress.kubernetes.io/backends` annotation on Ingress `slidekick` |
| Old version still serving | Build or sync not done | The `ship` skill |
| Sync fails: resource not permitted | AppProject `slidekick` allows only namespace `slidekick`, and no cluster-scoped resources | Keep `deploy/` in `slidekick`. Run ingest Jobs with `kubectl apply` |
| No traces in Dash0 | An `opentelemetry-*` package in the image (the operator turns itself off), uvicorn `--workers` or `--reload`, or a namespace without a `Dash0Monitoring` resource | Remove the package or the flag, or ask before adding monitoring to a namespace |

## 4. Fix, ship, confirm

Make the smallest change that removes the cause, and ship it through a PR (or straight to `main` if it's small) with the `ship` skill. For a Secret fix, check that the app's authenticated call works, without printing the credential. Confirm `issues` no longer lists the problem.

## 5. When it's not obvious

Suggest an AI investigation, and start one only when the person asks.
- **Radar:** if the `/mcp` connection advertises `start_investigation`, follow "Delegate a real question to hosted AI SRE" in `examples/agent-workflows/README.md`, with namespace `slidekick`. Otherwise give the person the resource's link, where the sparkle button at the top right starts one: `https://app.radarhq.io/c/<cluster-id>/resources/deployments?resource=slidekick/app`.
- **Dash0:** ask Agent0 for the root cause of the failing service or span.

Neither applies fixes. Check their findings against primary evidence, and fix through git.

## Report

The cause, with the evidence that shows it; the commit or Secret change that fixed it; and what you checked to confirm.
