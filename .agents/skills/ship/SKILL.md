---
name: ship
description: Deploy a merged change to slidekick.us and prove it's live. Use after a PR merges or a small change lands on main, when asked to ship, deploy or release, or to check that the latest commit is running.
---

# Ship a change and prove it's live

A merge to `main` builds the image, the bot commits the new tag, and Argo CD syncs namespace `slidekick`. A merge is not a deploy: you're done when https://slidekick.us shows the new version.

Every Radar call takes `cluster`: call `list_clusters` once and use our cluster's `id`.

## 1. Note the commit

Take the merge commit's full SHA from `gh pr view <number> --json mergeCommit`, or from `git rev-parse HEAD` before a direct push. Its first seven characters are the image tag.

The Action skips pushes that change only Markdown or `deploy/kustomization.yaml`. Markdown needs no deploy. For a kustomization-only change, skip step 2, keep the current image tag as the expected version, and check the config change after the sync.

## 2. Wait for the build, then pull its deploy commit

`gh run list --workflow build-and-deploy --commit <full-sha>`, then `gh run watch <id> --exit-status`. Match the run to the commit; don't take the latest one. If it isn't listed yet, retry for up to 5 minutes. If it failed, read `gh run view <id> --log-failed`, fix it, and ship again.

After a green run, `git switch main` and `git pull --rebase` to get the bot's `deploy: <tag> [skip ci]` commit. Without it, your next push is rejected.

## 3. Sync

Argo CD polls every few minutes. Don't wait: call `manage_gitops` with `tool: argocd`, `action: sync`, `namespace: argocd`, `name: slidekick`. The Application has no `syncOptions`; if it ever gets some, read the live list and pass all of it as `sync_options`.

Then `get_resource` on the Application (group `argoproj.io`) and check:
- `status.sync.revision` equals `git rev-parse origin/main`;
- `status.sync.status` is `Synced`, and `status.health.status` is `Healthy`, or `Progressing` while pods start.

If Application `slidekick` is missing, apply `argocd/project.yaml` and then `argocd/application-byo.yaml` with `apply_resource`. Never apply a second Application for `deploy/`: two would fight over the same resources.

## 4. Wait for the rollout

`get_resource` on Deployment `app` in `slidekick`, and confirm its image tag matches `newTag` in the remote `deploy/kustomization.yaml`. A healthy old revision isn't proof. Then `wait_for` with `kind: deployment`, `name: app`, `namespace: slidekick`, repeating (each call waits up to 45 seconds) for up to 5 minutes. If `deploy/` has a Rollout, use the `canary-release` skill.

`ImagePullBackOff` with 401 or 403 means the GHCR package is private. Only the person can fix that: repo page > Packages > Package settings > Change visibility > Public. "Not found" means the tag wasn't built: back to step 2.

## 5. Verify from outside

`curl -s https://slidekick.us/` must show `version <tag>`. The load balancer takes up to a minute to mark new pods healthy, so a brief mix of old and new is normal. Anything red (a failed sync, pods not ready, the old version still serving, a 502) goes to the `debug-failure` skill. Don't push guesses.

## Don't

- Call a Radar tool without `cluster`.
- `apply_resource` or `patch_resource` what Argo CD owns: Deployment, Service and BackendConfig `app`. Self-heal reverts it.
- Edit `newName` or `newTag` in `deploy/kustomization.yaml`. The Action owns them.
- Report "deployed" after the merge. Report what step 5 showed.
