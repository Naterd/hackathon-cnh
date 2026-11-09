#!/usr/bin/env bash
# Install GitHub's actions-runner-controller and our runner scale set on the cnh cluster.
# Before this, create the runner token Secret (a fine-grained PAT on this repo, Administration read/write):
#   kubectl create namespace arc-runners
#   pbpaste | kubectl -n arc-runners create secret generic gh-runner-token --from-file=github_token=/dev/stdin
# Also needs CNH_PROJECT and CNH_BUCKET (source <hackathon>/infra/config.env), ELASTIC_URL (set -a; source
# <hackathon>/.env), and the first time, CI's Elasticsearch key on the clipboard (see below).
# Re-running is safe: helm upgrades in place.
set -euo pipefail
cd "$(dirname "$0")"
VERSION=0.15.1
CHARTS=oci://ghcr.io/actions/actions-runner-controller-charts

ctx=$(kubectl config current-context)
[[ $ctx == *_cnh ]] || { echo "kubectl context is $ctx, not the cnh cluster" >&2; exit 1; }
kubectl -n arc-runners get secret gh-runner-token >/dev/null || { echo "create the gh-runner-token Secret first" >&2; exit 1; }

helm upgrade --install arc "$CHARTS/gha-runner-scale-set-controller" --version "$VERSION" \
  --namespace arc-systems --create-namespace --wait
kubectl apply -f rbac.yaml

# CI's bucket access (rbac.yaml explains why): read everything, write only under derived/ci/ and eval/
num=$(gcloud projects describe "$CNH_PROJECT" --format='value(projectNumber)')
ci="principalSet://iam.googleapis.com/projects/$num/locations/global/workloadIdentityPools/$CNH_PROJECT.svc.id.goog/namespace/ingest-ci"
objs="projects/_/buckets/$CNH_BUCKET/objects"
gcloud storage buckets add-iam-policy-binding "gs://$CNH_BUCKET" --member="$ci" \
  --role=roles/storage.objectViewer --condition=None >/dev/null
gcloud storage buckets add-iam-policy-binding "gs://$CNH_BUCKET" --member="$ci" --role=roles/storage.objectUser \
  --condition="title=ci-writes,expression=resource.name.startsWith('$objs/derived/ci/') || resource.name.startsWith('$objs/eval/')" >/dev/null

# CI's Elasticsearch key: it can read the live index and write only kubecon-moments-ci. An API key can't
# create a key with privileges, so make it in Kibana: API keys > Create, expire in 14 days, turn on
# "Control security privileges", paste es-ci-key.json. Copy the encoded key, then re-run this script.
if ! kubectl -n ingest-ci get secret elastic >/dev/null 2>&1; then
  printf 'ELASTIC_URL=%s\nELASTIC_ES_API_KEY=%s\n' "$ELASTIC_URL" "$(pbpaste)" |
    kubectl -n ingest-ci create secret generic elastic --from-env-file=/dev/stdin
fi

helm upgrade --install slidekick-gke "$CHARTS/gha-runner-scale-set" --version "$VERSION" \
  --namespace arc-runners -f runner-values.yaml --wait
kubectl -n arc-systems get pods
