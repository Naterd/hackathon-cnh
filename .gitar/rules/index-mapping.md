---
title: "Index mapping changes"
description: "Mapping or index-name changes need a reindex plan"
type: check
when: "PRs that change the Elasticsearch index mapping, the index name, or the kubecon-moments alias"
actions: "Add the label needs-reindex and comment if the PR has no reindex or alias-swap plan"
---

# Index mapping changes

The app and the ingest Jobs read alias `kubecon-moments`, which points at `kubecon-moments-v1`. A new or changed field, or a different index name, needs a new index (for example `kubecon-moments-v2`), a reindex or a re-run of ingest into it, and an alias swap. Otherwise existing documents won't match the new mapping.

When a PR changes the mapping, an index name, or the alias:
- Add the label `needs-reindex`.
- If the PR description doesn't say how the data gets into the new mapping and when the alias moves, post a comment asking for that plan.
