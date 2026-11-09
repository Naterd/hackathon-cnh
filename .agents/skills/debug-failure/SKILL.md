---
name: debug-failure
description: Find the root cause of a failure in this team's cluster at the hackathon and fix it in git. Use when the app is down, slow, crash-looping, not updating, returning errors, or a deploy or Argo CD sync failed.
---

# Debug a failure, fix it in git

Work from evidence: read what Radar shows, name the cause, then change the repo. Argo CD reverts direct edits to resources it owns, so fixes to those resources ship as commits. Non-git credential Secrets follow the [agent workflows](../../../examples/agent-workflows/README.md) recipe; never commit their values.

The namespace is `team-<team>`, from `namespace:` in `deploy/kustomization.yaml`, and Radar Cloud lists the team's cluster as `<team>`.

**Pass `cluster` and `namespace` to every Radar MCP call.** `cluster` is the `id` of the `list_clusters` entry whose `name` is exactly `<team>`. The organization has about 40 team clusters plus `cnh`; evidence from any other cluster is not this team's.

## 1. Stop the damage first

- **Canary crash-looping or failing its checks** (`deploy/` uses `rollout.yaml`): call `manage_rollout` with `action: abort`. A canary that is only paused at its step isn't broken: that's the person's release decision, so don't abort it; use the `canary-release` skill. The stable version takes all traffic back at once. The next pushed version starts a fresh canary.
- **Plain Deployment broken after a push**: the rollback is `git revert <bad commit>` and a push. Don't use `manage_gitops` rollback: auto-sync puts `main` back.

Tell the person what you did before you go deeper.

## 2. Gather evidence

1. Call `issues` for the namespace. It lists what's broken now, grouped and with a reason.
2. Call `get_changes` for the namespace, `since: 1h`, to see what changed just before it broke. Image, env and probe diffs are listed first.
3. Call `diagnose` on the failing workload: `kind: deployment`, or `kind: rollout` with `group: argoproj.io`, and `name: app`. You get logs, events and startup blockers in one call. For a sync problem, diagnose `kind: application` with `group: argoproj.io`.
4. If you need more:
   - `get_pod_logs` with `previous: true` gives the crash's own output;
   - `get_events` covers scheduling, and the host's refusals and the budget (Warning `SyncError` on the pod);
   - `get_resource` on the Application (`status.conditions`, `status.operationState`) covers sync errors.

## 3. Common causes in this cluster

| Symptom | Usual cause | Fix |
| --- | --- | --- |
| `ImagePullBackOff`, 401/403/denied | The GHCR package is still private | The person makes it public: repo > Packages > Package settings > Change visibility |
| `ImagePullBackOff`, not found | The tag wasn't built (the Action failed) | `gh run view --log-failed`, fix the build |
| `CreateContainerConfigError`, missing Secret or key | The referenced non-git Secret or key is missing or misspelled | Have the person create or correct the Secret in Radar’s resource editor. Keep values out of git and agent output. Restart if an already-running app needs updated environment variables; correct a wrong Secret reference in git. |
| `OOMKilled`, exit 137 | The container passed `limits.memory` | Fix the app's memory use, or raise the limit in `deploy/` (the team's budget caps limits at 8 GiB in total) |
| `CrashLoopBackOff` | The app exits at startup | `get_pod_logs` with `previous: true` |
| Ready 0/N, readiness failing | The app isn't on `$PORT` (8080), or `/readyz` doesn't answer 200 | Fix the app, or the probe paths in `deploy/` |
| Pod `Pending`, Warning `SyncError` saying `exceeded quota: team-quota` | Over the team's budget, one total across every namespace in the cluster: 2 CPU / 4 GiB of requests, 4 CPU / 8 GiB of limits | Lower `replicas` or `requests`, or remove something you installed |
| Pod `Pending`, any other Warning `SyncError` | The host refused the pod: it asks for privileged, hostPath, hostNetwork or hostPID. The event's message has the host's reason | Drop that setting (for a Helm chart, in its values). The host's rules don't bend |
| Version differs from the expected image tag | Build or sync has not completed | Follow the `ship` skill. A kustomization-only fix intentionally keeps the old app version; verify its configuration instead. |
| URL 404 or 503, pods healthy | Service `app` in `team-<team>` is missing, renamed, not on port 80, or selects no ready pods. The platform routes the URL to that Service; the HTTPRoute in `deploy/` plays no part | `diagnose` the Service (`kind: service`, `name: app`) with `probe: true`, and fix it in `deploy/`. If the Service is fine, ask an organizer |
| Sync fails: namespace not found, or `metadata.annotations: Too long` on a CRD | The Application needs `CreateNamespace=true` or `ServerSideApply=true` | Add them to its `syncOptions`, read the live Application, and pass its complete current list as `sync_options`; follow the `ship` skill’s version-specific guidance |

## 4. Fix, ship, confirm

Make the smallest change that removes the cause. For repo changes, push and follow the `ship` skill through step 5: verify the built SHA, or the unchanged image tag plus the intended configuration for a deliberately skipped build. For a non-git Secret correction, verify the app's authenticated operation without printing the credential. Confirm `issues` no longer lists the problem.

## 5. When it's not obvious

Suggest a hosted AI investigation. Hosted investigations cost the team nothing at the hackathon: they come from the organization's allowance. Once the person has asked for one, use the hosted tools if the organization-level `/mcp` connection advertises `start_investigation`; follow `examples/agent-workflows/README.md`. Otherwise give the person the failing resource's Radar Cloud link (format in AGENTS.md, "A real symptom you can't explain"): the sparkle button at the top right starts the investigation. The investigation tools do not apply remediation. Verify findings against primary evidence and apply fixes through the resource’s owner. New interactive runs, including MCP starts, are private unless the person shares them; sharing shows them to every team in the org.

## Report

Give the person three things: the cause, with the evidence that shows it; the commit or non-git Secret correction that fixed it; and what you checked to confirm it's fixed.
