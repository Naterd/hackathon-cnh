---
name: demo-story
description: Write SlideKick's 5:30 demo story across the four judged tracks (Radar, Dash0, Gitar, Elasticsearch), with the evidence for each and the pages to screenshot. Use when asked for the demo, the story, the 5:30 presentation, or a summary of the day.
---

# The 5:30 story

Each track's judges want to see their tool used for real, and one real decision a tool helped make beats a list of features. Read only, except `STORY.md`. Radar's times are UTC: give every time in local time (`date`).

## 1. Gather the evidence

- **What we built:** `git log --since=midnight --no-merges` on `main` (the bot's `deploy:` commits only mark builds), and what https://slidekick.us does now. Who it helps at a conference.
- **Radar:** `get_changes` for `slidekick` and `ingest`, with `since: 10h` and `limit: 50`. Pick the moments: the first deploy, features shipped, what broke and how it was found and fixed, canaries promoted or aborted. Then `get_cluster_audit` for both namespaces, compared with `## Before` in `STORY.md`: what was fixed, what's new, and what's left, with a reason.
- **Investigations:** you can't list hosted Radar investigations, so ask which one they ran and what it found, and likewise for Agent0 in Dash0. If there's none, say so now: there's still time, and an all-clear counts.
- **Gitar:** `gh pr list --state merged --limit 50`, then `gh pr view <number> --comments` on the interesting ones. Count the reviewed PRs, and pick the finding that caught a real bug and any CI fix Gitar's auto-apply pushed.
- **Dash0:** the *SlideKick: Four Golden Signals* dashboard over the day, including the ingest pipeline section (talks and documents indexed), plus any synthetic check, alert or SLO.
- **Elasticsearch:** one lookup end to end: the photo, the hybrid query, the match confidence, and the YouTube link at the right second.

## 2. Write it

Add `## The story` to `STORY.md`: about 6 bullets, each one sentence with its evidence (a commit, a PR, a change, a finding):
- what we built, and who it helps;
- how we shipped: PRs reviewed by Gitar, merges synced by Argo CD through Radar, how many releases;
- what broke or what we checked, and what Radar and Dash0 showed;
- the investigations and what we did with them;
- what Elasticsearch does in one lookup;
- Checks before and after.

## 3. Screenshots, with links

- Radar's timeline: `https://app.radarhq.io/c/<cluster-id>/timeline`.
- The Application: `https://app.radarhq.io/c/<cluster-id>/resources/applications?resource=argocd/slidekick&apiGroup=argoproj.io`.
- Radar's Checks: `https://app.radarhq.io/checks?scope=<cluster-id>&namespace=slidekick`, next to the morning's.
- The Radar investigation and the Agent0 answer.
- Dash0's golden-signals dashboard.
- The Gitar review that caught something, on its PR.
- A lookup on a phone: the photo in, the talk and timestamp out.

Show them the story and the list, and ask before you commit `STORY.md`.
