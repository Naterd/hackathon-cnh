#!/usr/bin/env bash
# Run the SlideKick ingest on our GKE cluster.
#
#   ingest/run.sh extract [--limit N] [--talks id,id] [--parallel P] [--force]
#   ingest/run.sh index   [--index NAME] [--alias kubecon-moments] [--limit N] [--talks id,id] [--replace]
#   ingest/run.sh status
#   ingest/run.sh logs extract|index
#
# extract: one pod per talk cuts slide stills, OCRs them and attaches captions, writing to
#          gs://$CNH_BUCKET/derived/$DERIVED/$CNH_EVENT/<id>/. Finished talks are skipped unless --force.
# index:   one pod loads everything extracted so far into Elasticsearch (Secret ingest/elastic).
#
# Needs CNH_BUCKET (and kubectl pointed at the cnh cluster): source the hackathon infra/config.env.
# The code ships as a ConfigMap from this checkout, so a code change needs no image build.
# BOOTSTRAP=1 installs ffmpeg and the OCR libraries at pod start on python:3.12-slim, for testing
# before the ingest image exists. Otherwise INGEST_IMAGE (default below) must already hold them.
set -euo pipefail
cd "$(dirname "$0")"

: "${CNH_BUCKET:?source the hackathon infra/config.env first}"
EVENT=${CNH_EVENT:-kubecon-na-2024}
DERIVED=${DERIVED:-v1}
IMAGE=${INGEST_IMAGE:-ghcr.io/naterd/hackathon-cnh/ingest:latest}
NS=ingest
VERSION=$(git rev-parse --short HEAD 2>/dev/null || echo dev)

ctx=$(kubectl config current-context)
[[ $ctx == *_cnh ]] || { echo "kubectl context is $ctx, not the cnh cluster" >&2; exit 1; }

cmd=${1:-}; shift || true
LIMIT="" TALKS="" PARALLEL=96 FORCE=0 INDEX=kubecon-moments-$DERIVED ALIAS="" REPLACE=0
while (( $# )); do
  case $1 in
    --limit)    LIMIT=$2; shift ;;
    --talks)    TALKS=$2; shift ;;
    --parallel) PARALLEL=$2; shift ;;
    --force)    FORCE=1 ;;
    --index)    INDEX=$2; shift ;;
    --alias)    ALIAS=$2; shift ;;
    --replace)  REPLACE=1 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
  shift
done

render() {  # template file, then KEY=value pairs
  local f=$1; shift
  local args=()
  for kv in "$@"; do args+=(-e "s|__${kv%%=*}__|${kv#*=}|g"); done
  sed "${args[@]}" "$f"
}

ship_code() {
  kubectl -n $NS create configmap ingest-code --from-file=extract.py --from-file=index.py --from-file=telemetry.py \
    --from-file=requirements.txt --dry-run=client -o yaml | kubectl apply -f - >/dev/null
}

case $cmd in
  extract)
    if [[ -n $TALKS ]]; then
      ids=$(tr ',' '\n' <<<"$TALKS")
    else
      ids=$(gcloud storage ls "gs://$CNH_BUCKET/$EVENT/*/.done" | awk -F/ '{print $(NF-1)}' | sort -u)
    fi
    [[ -n $LIMIT ]] && ids=$(head -n "$LIMIT" <<<"$ids")
    count=$(grep -c . <<<"$ids")
    (( PARALLEL > count )) && PARALLEL=$count
    ship_code
    kubectl -n $NS create configmap ingest-talks --from-literal=talks.txt="$ids" \
      --dry-run=client -o yaml | kubectl apply -f - >/dev/null
    setup=""
    if [[ ${BOOTSTRAP:-0} == 1 ]]; then
      IMAGE=python:3.12-slim
      setup="apt-get update -qq \&\& apt-get install -y -qq --no-install-recommends ffmpeg libgl1 libglib2.0-0 >/dev/null \&\& pip install -q -r /code/requirements.txt \&\&"
    fi
    kubectl -n $NS delete job ingest-extract --ignore-not-found >/dev/null
    render k8s/extract-job.yaml COUNT="$count" PARALLEL="$PARALLEL" IMAGE="$IMAGE" SETUP="$setup" \
      EVENT="$EVENT" DERIVED="$DERIVED" BUCKET="$CNH_BUCKET" FORCE="$FORCE" VERSION="$VERSION" | kubectl apply -f -
    echo "extracting $count talks, $PARALLEL at a time. Progress: ingest/run.sh status"
    ;;
  index)
    args=""
    [[ -n $ALIAS ]] && args+=" --alias $ALIAS"
    [[ -n $LIMIT ]] && args+=" --limit $LIMIT"
    [[ -n $TALKS ]] && args+=" --talks=$TALKS"  # = form: ids can start with "-"
    [[ $REPLACE == 1 ]] && args+=" --replace"
    ship_code
    kubectl -n $NS delete job ingest-index --ignore-not-found >/dev/null
    render k8s/index-job.yaml EVENT="$EVENT" DERIVED="$DERIVED" BUCKET="$CNH_BUCKET" \
      INDEX="$INDEX" ARGS="$args" VERSION="$VERSION" | kubectl apply -f -
    echo "indexing into $INDEX. Follow it: ingest/run.sh logs index"
    ;;
  status)
    kubectl -n $NS get jobs -l app=ingest \
      -o custom-columns='JOB:.metadata.name,DONE:.status.succeeded,FAILED:.status.failed,ACTIVE:.status.active,TOTAL:.spec.completions' 2>/dev/null
    n=$(gcloud storage ls "gs://$CNH_BUCKET/derived/$DERIVED/$EVENT/*/segments.json" 2>/dev/null | grep -c . || true)
    echo "extracted talks in derived/$DERIVED: $n"
    ;;
  logs)
    kubectl -n $NS logs -l "app=ingest,step=${1:-extract}" -c "${1:-extract}" --tail=20 --prefix --max-log-requests 20
    ;;
  *)
    sed -n '2,17p' "$0"; exit 2 ;;
esac
