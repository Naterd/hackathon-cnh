---
name: get-started
description: Connect a teammate's tools to SlideKick (git, Radar, kubectl, Dash0, Gitar), then lay out what's left for the day. Use when someone asks to get set up, to check their setup, or what to do first, and at the start of a first session in this repo.
---

# Get set up

GitOps, Dash0 and Gitar already run, so this only connects the person's tools. Check, then fix. Keep replies short: at a check that fails, say in a sentence or two what to do, and wait. Don't run `scripts/setup.sh`: it's for the hackathon's shared clusters.

## Check

1. **Git.** `git config core.hooksPath` should print `.githooks`. If it prints nothing, set it with `git config core.hooksPath .githooks`. If it prints another path, a tool manages it: leave it, and pull by hand before each commit.
2. **Radar.** If you have no Radar tools, or a call says the server needs authentication, the person signs in; you can't do it for them. In Claude Code, they run `claude mcp add --scope user radar-cloud --transport http https://api.radarhq.io/mcp` in a terminal and restart, then `/mcp` > radar-cloud > **Authenticate**, choosing our organization, not Cloud Native Hackathon. Then call `list_clusters`, and `list_resources` with `kind: deployments`, `namespace: slidekick`: Deployment `app` should be there.
3. **kubectl**, for ingest Jobs and Secrets. `kubectl -n slidekick get deploy app`. If it fails, the person gets credentials with the `gcloud container clusters get-credentials` command in `docs/details.md`.
4. **Dash0.** `~/.dash0-auth-header` exists, made with the commands in `docs/details.md` (Observability (Dash0)). Never print it. If their agent supports it, connect the Dash0 MCP server: https://dash0.com/docs/dash0/ai/mcp.
5. **Gitar and GitHub.** Gitar needs nothing installed: it reviews every PR. `gh auth status` must work, since PRs go through `gh`.

## The day

In a few lines, from the status table in `docs/details.md` and these:
- **Radar:** Checks before and after, and one AI investigation; an all-clear on a healthy app counts. If `STORY.md` has no `## Before` yet, record one from `get_cluster_audit` for `slidekick` and `ingest`: the time, findings by severity, one line each. Show it, and ask before committing.
- **Dash0:** one Agent0 root cause on a real issue, a synthetic check on `/healthz`, a p95 SLO, and browser monitoring.
- **Gitar:** every large change through a PR with its findings fixed, and a PR check workflow so auto-apply has failing CI to fix.
- **Elasticsearch:** the hybrid lookup working end to end.
- **By 5:30:** the `demo-story` skill.

Then ask what they want to work on.
