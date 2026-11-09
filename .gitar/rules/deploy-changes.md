---
title: "Deploy changes"
description: "Flag PRs that change what Argo CD deploys"
type: automation
when: "PRs that add or modify files under deploy/ or argocd/"
actions: "Add the label deploys and post a comment with the after-merge checks"
---

# Deploy changes

Merging a change under `deploy/` or `argocd/` rolls the live app at https://slidekick.us through Argo CD.

When a PR touches either folder:
- Add the label `deploys`.
- Post one comment: "Merging this rolls the app. After merge: confirm Application `slidekick` is Synced and Healthy in Radar, and that https://slidekick.us shows the new version. Roll back with `git revert` on `main`; don't re-run an old build-and-deploy run."
