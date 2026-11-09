#!/usr/bin/env bash
# Install GitHub's actions-runner-controller and our runner scale set on the cnh cluster.
# Before this, create the runner token Secret (a fine-grained PAT on this repo, Administration read/write):
#   kubectl create namespace arc-runners
#   pbpaste | kubectl -n arc-runners create secret generic gh-runner-token --from-file=github_token=/dev/stdin
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
helm upgrade --install slidekick-gke "$CHARTS/gha-runner-scale-set" --version "$VERSION" \
  --namespace arc-runners -f runner-values.yaml --wait
kubectl -n arc-systems get pods
