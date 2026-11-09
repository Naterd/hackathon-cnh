---
name: canary-release
description: Ship a change to slidekick.us as an Argo Rollouts canary, check it, then promote or abort it through Radar. Use when asked for a canary, progressive delivery, a safe or gradual release, to promote or abort a rollout, or to switch the app from a Deployment to a Rollout.
---

# Canary release: ship, check, promote or abort

Argo Rollouts runs in namespace `argo-rollouts`. Every Radar call takes `cluster` (our cluster's `id` from `list_clusters`) and `namespace: slidekick`.

The steps come from `examples/canary/rollout.yaml`: 25% of the pods get the new version, it pauses until someone promotes it, then 50% for 60 seconds, then 100%. The split is by pod count, because the load balancer sends traffic to every ready pod behind Service `app`. With 2 replicas the first step is 1 of 2 pods, so use 4 for 1 in 4.

## 0. Turn it on (once)

Skip this if `deploy/kustomization.yaml` lists `rollout.yaml`.

1. Create `deploy/rollout.yaml` from `deploy/deployment.yaml`: `apiVersion: argoproj.io/v1alpha1`, `kind: Rollout`, `replicas: 4`, and the `strategy.canary` block from `examples/canary/rollout.yaml`. Keep the rest of our Deployment (labels, probes, resources, `securityContext`); the example's pod template is the starter's.
2. In `deploy/kustomization.yaml`, replace `deployment.yaml` with `rollout.yaml`, and delete `deploy/deployment.yaml`. If `deploy/` has an HPA, point its `scaleTargetRef` at the Rollout and drop `replicas`.
3. Ship it through a PR and the `ship` skill, with `kind: rollout` in `wait_for`. The first Rollout goes straight to 100%: there's no older version to protect.

## 1. Ship the new version

Merge the change and follow the `ship` skill through step 3. Then `wait_for` with `kind: rollout`, `name: app`, `condition: jsonpath={.status.phase}=Paused`, repeating for up to 5 minutes. It's now at 25%.

## 2. Check the canary before promoting

- `get_resource` on the Rollout (group `argoproj.io`): the canary ReplicaSet's pods are ready.
- `issues` for `slidekick` doesn't list the Rollout or its pods.
- `curl -s https://slidekick.us/` about 8 times. None may be errors. Some answers usually show the new `version <tag>`. If none of 8 do, that's chance: try 8 more, or read the canary pod's logs. Only errors fail this check.
- If the pods carry `app.kubernetes.io/version`, compare the canary's error rate and latency with the stable version's in Dash0.

Show the person what you saw, and promote only when they agree, unless they already told you to promote a healthy canary. If anything fails, go to step 4.

## 3. Promote

`manage_rollout` with `action: promote`. It moves to 50%, then to 100% after 60 seconds. `wait_for` with `kind: rollout`, `name: app` until done, then check with curl that only the new version answers. Use `promote-full` only when the person asks for it.

## 4. Abort

`manage_rollout` with `action: abort`. The stable version takes every pod back at once. Check with curl that only the old version answers, then find the cause with the `debug-failure` skill and fix it in git. The next version starts a fresh canary. Use `retry` only to re-run the same version, for example after fixing a Secret.

## Don't

- Change the Rollout with `patch_resource` or `apply_resource`: Argo CD reverts it. Steps, replicas and images change in git.
- Promote a canary whose pods aren't ready, or that `issues` flags.
