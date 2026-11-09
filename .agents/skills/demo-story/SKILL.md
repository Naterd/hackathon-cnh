---
name: demo-story
description: Write the team's 5:30 demo story from Radar, covering what changed today, Checks before and after, and the investigation, plus which Radar Cloud pages to screenshot. Use when asked for the demo, the story, the 5:30 presentation, or a summary of the day.
---

# The 5:30 story

The judges look for: the project running on Kubernetes with Radar connected, Radar's UI and MCP used throughout, an AI investigation and what came of it, and something that makes conferences easier. One real decision Radar helped make beats a list of features. Read only, except `STORY.md`.

The team is `<team>` from `namespace: team-<team>` in `deploy/kustomization.yaml`. Call `list_clusters` and use the `id` of the entry named exactly `<team>` as `cluster`.

1. **What happened.** Call `get_changes` with `namespace: team-<team>`, `since` covering the day (say `10h`), and `limit: 50`; repeat for each namespace they added. Read `git log` since the morning; the bot's `deploy:` commits only mark builds. Pick the moments: the first deploy, features shipped, what broke and how it was found and fixed, releases (canaries promoted or aborted). Radar's times are UTC: give every time in the person's local time (the laptop's timezone, from `date`).
2. **Checks after.** Call `get_cluster_audit` for `team-<team>` and each namespace they added. Compare with `## Before` in `STORY.md`: what was fixed, what's new, and what's left (with a reason). If there's no `## Before`, say so and use their morning screenshot.
3. **The investigation.** You can't list hosted investigations. Ask which one they ran and what it found. Only the person who started it can open it, unless they shared it: they open it at `https://app.radarhq.io/c/<cluster-id>/investigations`. If they haven't run one, say so now: there's still time, and an all-clear counts.
4. **Write it.** Add `## The story` to `STORY.md`: 5 bullets, each one sentence with its evidence (a commit, a change, a finding):
   - what we built, and who it helps at a conference;
   - how we shipped: push, Argo CD sync through Radar, how many releases;
   - what broke, or what we checked, and what Radar showed;
   - the investigation and what we did with it;
   - Checks before and after.
5. **Screenshots to take,** each with its link (`https://app.radarhq.io/c/<cluster-id>/…`):
   - `timeline`: the day's changes;
   - the Application `app`: `resources/applications?resource=team-<team>/app&apiGroup=argoproj.io`;
   - Checks: the whole list (`https://app.radarhq.io/checks?scope=<cluster-id>`, the platform's findings show what Radar catches), and the team's (`https://app.radarhq.io/checks?scope=<cluster-id>&namespace=team-<team>`) next to the morning's;
   - the investigation (step 3).

Show them the story and the list, and ask before you commit `STORY.md`.
