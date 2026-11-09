---
name: canary-release
description: Ship a change as an Argo Rollouts canary in this team's cluster at the hackathon, check it, then promote or abort it through Radar. Use when asked for a canary, progressive delivery, a safe or gradual release, to promote or abort a rollout, or to switch the app from a Deployment to a Rollout.
---

# Canary release: ship, check, promote or abort

The namespace is `team-<team>`, from `namespace:` in `deploy/kustomization.yaml`, and the URL is `https://<team>.cnh.radarhq.io`. The Argo Rollouts controller is already installed in the team's cluster.

**Pass `cluster` and `namespace` to every Radar MCP call.** `cluster` is the `id` of the `list_clusters` entry whose `name` is exactly `<team>`. The organization has about 40 team clusters plus `cnh`; never use another one.

The canary steps are in `deploy/rollout.yaml`:
1. 25% of the pods get the new version;
2. it pauses until someone promotes it;
3. 50%;
4. a 60-second pause;
5. 100%.

The split is by pod count (1 in 4 pods), not by request weight: the URL goes straight to Service `app`, with no traffic router for Argo Rollouts to drive.

## 0. Turn it on (once)

Skip this if `deploy/kustomization.yaml` already lists `rollout.yaml`.

1. Copy `examples/canary/rollout.yaml` to `deploy/rollout.yaml`. In `deploy/kustomization.yaml`, replace `deployment.yaml` with `rollout.yaml` under `resources:`, and delete `deploy/deployment.yaml`.
2. If the HPA is on, point its `scaleTargetRef` at `apiVersion: argoproj.io/v1alpha1`, `kind: Rollout`, and delete `replicas:` from the Rollout.
3. Ship it with the `ship` skill, using `kind: rollout` in `wait_for`. The first Rollout goes straight to 100%: there's no older version to protect.

## 1. Ship the new version

Push the change and follow the `ship` skill through step 3 (build, sync). Then call `wait_for` with `kind: rollout`, `name: app`, `condition: jsonpath={.status.phase}=Paused`, repeating up to about 5 minutes. It's now at the 25% step.

## 2. Check the canary before promoting

- `get_resource` on the Rollout (`group: argoproj.io`): check that the canary ReplicaSet's pods are ready.
- `issues` for the namespace must not list the Rollout or its pods.
- Call `curl -s https://<team>.cnh.radarhq.io/` about 8 times. None may be errors. At 25%, some answers usually show the new version (the starter prints `version <sha>`). If none of the 8 do, that's chance (about 1 time in 10), not a failure: try 8 more, or read the canary pod's logs. Only errors fail this check.

If anything fails, go to step 4. Show the person what you saw, and promote only when they agree, unless they've already told you to promote a healthy canary.

## 3. Promote

Call `manage_rollout` with `action: promote`. That moves it to 50%, and after 60 seconds it goes to 100% by itself. Then call `wait_for` with `kind: rollout` and `name: app` (the default condition is the full rollout), repeating until done. Check with curl that only the new version answers.

Use `promote-full` (skip every step) only when the person asks for it.

## 4. Abort

Call `manage_rollout` with `action: abort`. The stable version takes all the pods back at once, and the rollout stays aborted. Check with curl that only the old version answers.

Then find the cause with the `debug-failure` skill, fix it in git, and push. The new version starts a fresh canary at step 1. Use `retry` only to re-run the same version, for example after fixing a config outside the image.

## Don't

- Don't change the Rollout with `patch_resource` or `apply_resource`. Argo CD reverts it. Steps, replicas and images all change in git.
- Don't promote a canary whose pods aren't ready or that `issues` flags.
