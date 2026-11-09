---
name: ship
description: Deploy a change to this team's cluster at the hackathon and confirm it is live. Use after editing app/ or deploy/, when asked to ship, deploy, release or push a change, or to check that the latest commit is running. Covers first-time GitOps setup too.
---

# Ship a change and prove it's live

Deploys are GitOps: you push to `main`, the GitHub Action builds the image and commits the new tag, and Argo CD (inside the team's cluster) syncs the namespace. A push is not a deploy. You're done only when the new version answers at the team URL.

The team name is in `deploy/kustomization.yaml` (`namespace: team-<team>`). Radar Cloud lists the team's cluster as `<team>`. The namespace is `team-<team>`, the Argo CD Application is `app` in that namespace, and the URL is `https://<team>.cnh.radarhq.io`.

**Every Radar MCP call below takes `cluster` and `namespace: team-<team>`.** Call `list_clusters` once, take the `id` of the entry whose `name` is exactly `<team>`, and pass it as `cluster`. The organization has about 40 team clusters plus `cnh`, so the tools can't pick a default, and the person only has access to this team's cluster.

## 0. First time only

Skip this if `get_resource` (kind `Application`, group `argoproj.io`, name `app`) finds the Application.

1. If `deploy/kustomization.yaml` still says `team-__TEAM__`, run `./scripts/setup.sh <team>` first. Ask for the team name if you don't know it.
2. Push (step 1 below) and wait for the first build (step 2).
3. The GHCR image must be public, and usually already is. Don't check it up front: new pods that pull it and run (step 4) prove it.
4. Apply `argocd/application.yaml` with `apply_resource` (with `cluster`). The starter app is already running; Argo CD takes it over. Keep the app’s reconciled manifests in GitOps. Direct Secrets and temporary Jobs follow the [agent workflows](../../../examples/agent-workflows/README.md) recipe.

## 1. Push

Commit and push to `main`. Note the full commit SHA (`git rev-parse HEAD`) and its seven-character image tag (`git rev-parse --short=7 HEAD`).

The Action skips pushes changing only Markdown and/or `deploy/kustomization.yaml`. Markdown-only changes need no deployment. For an intentional kustomization-only change, skip step 2, retain the existing image tag as the expected app version, and verify the configuration change after sync. Every other push, including other files under `deploy/`, triggers a build.

## 2. Wait for the build, then pull its deploy commit

With the GitHub CLI: `gh run list --workflow build-and-deploy --commit <full-sha>`, then `gh run watch <id> --exit-status` for that commit’s run. Do not use the latest run without matching its commit. If the run is not listed yet, retry briefly within the five-minute deadline; absence is not success. If the run is cancelled or superseded, report that this commit was not built. Verify a newer commit only if it contains the intended change and the person has authorized deploying it; otherwise report the blocker. Without the CLI, use the same deadline while checking `git fetch origin` until `origin/main` has a commit `deploy: <sha> [skip ci]`.

If the run failed, read `gh run view <id> --log-failed`, fix it, push again, and wait again.

**Then pull, after every green run.** Run `git pull --rebase` so your copy has the bot's `deploy: <sha> [skip ci]` commit. Do it even if you have nothing else to push yet: without it, your next push is rejected.

## 3. Sync

Argo CD polls git every few minutes. Don't wait for it: call `manage_gitops` with `cluster`, `tool: argocd`, `action: sync`, `namespace: team-<team>`, `name: app`.

The starter's Application has no `syncOptions` and needs none. If an Application has options (such as `ServerSideApply=true` for large CRDs or `CreateNamespace=true`), read the live Application immediately before syncing and pass its complete current list as `sync_options`. Do not use a cached list or pass only one added option. This preserves the options on Radar v1.19.0, which does not inherit them; Radar’s current sync implementation inherits them when omitted, but an explicit list replaces them for that sync.

Then `get_resource` on the Application and check both of these:
- `status.sync.revision` equals `git rev-parse origin/main` (after `git fetch`);
- `status.sync.status` is `Synced` and `status.health.status` is `Healthy`, or `Progressing` while pods start.

Right after the first sync, the Application can read `OutOfSync` for a moment: a sync started from Radar doesn't prune, so the starter's old `app-code` ConfigMap stays until Argo CD's own automatic sync removes it, seconds later. That's expected. Do step 4, then check the Application again: it goes `Synced` on its own.

## 4. Wait for the rollout

First read the live Deployment or Rollout with `get_resource` and confirm its app container image matches the expected image/tag from the remote `deploy/kustomization.yaml`. A healthy old workload does not prove this push deployed.

Call `wait_for` with `kind: deployment` (or `rollout` if `deploy/` uses `rollout.yaml`) and `name: app`. Each call waits at most 45 seconds, so repeat it until it reports done, for up to about 5 minutes. New pods running the new image also prove the image is public.

If the new pods show `ImagePullBackOff` or `ErrImagePull`, check the image without credentials, with `newName` (minus `ghcr.io/`) and `newTag` from `deploy/kustomization.yaml` (it uses shell variables, so Claude Code asks the person to approve it: say why first):
```bash
repo=<owner>/<repo>; tag=<tag>
token=$(curl -s "https://ghcr.io/token?scope=repository:$repo:pull" | sed -n 's/.*"token":"\([^"]*\)".*/\1/p')
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $token" -H 'Accept: application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json' "https://ghcr.io/v2/$repo/manifests/$tag"
```
`401` or `403` means it's private: ask the person to open the package on their repo page (Packages, right-hand column) > Package settings > Change visibility > Public. You can't do this for them. Wait until they confirm; the pods retry on their own within a few minutes. `404` means that tag wasn't built: see step 2.

A Rollout pauses at 25% on purpose. For that flow, use the `canary-release` skill.

## 5. Verify from outside

`curl -s https://<team>.cnh.radarhq.io/`. The starter app prints `version <sha>`, and it must match the built SHA from step 1, or the unchanged image tag for a deliberately skipped build. For a configuration-only change, also verify the intended configuration took effect. If you replaced the app, request something your change made visible.

If anything here is red (a failed sync, pods not ready, the old version still serving, a 404 or 503 from the URL), switch to the `debug-failure` skill. Don't push guesses.

## Don't

- Don't call a Radar tool without `cluster`.
- Don't apply Argo CD-owned app manifests with `apply_resource` or `patch_resource`. Argo CD's self-heal reverts them within seconds.
- Don't edit `newName` or `newTag` in `deploy/kustomization.yaml`; the Action owns them.
- Don't report "deployed" after the push. Report what step 5 showed.
