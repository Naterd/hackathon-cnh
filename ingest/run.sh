#!/usr/bin/env bash
# Run the SlideKick ingest on our GKE cluster.
#
#   ingest/run.sh extract [--limit N] [--talks id,id] [--parallel P] [--force]
#   ingest/run.sh index   [--index NAME] [--alias kubecon-moments] [--limit N] [--talks id,id] [--replace]
#   ingest/run.sh eval    [--n 200] [--seed 1] [--index kubecon-moments] [--min-talk1 0.5]
#                         [--modes screen,slide,projector,browser] [--upload-px 1280]
#   ingest/run.sh thumbs  [--talks id,id] [--force]
#   ingest/run.sh details [--index kubecon-details]
#   ingest/run.sh wait    extract|index|eval|thumbs|details   # until the Job finishes; prints logs; exit 1 if it failed
#   ingest/run.sh status
#   ingest/run.sh logs    extract|index|eval|thumbs|details
#
# extract: one pod per talk cuts slide stills, OCRs them and attaches captions, writing to
#          gs://$CNH_BUCKET/derived/$DERIVED/$CNH_EVENT/<id>/. Finished talks are skipped unless --force.
# index:   one pod loads everything extracted so far into Elasticsearch (Secret ingest/elastic).
# eval:    one pod renders N random moments as phone photos, runs the app's lookup on each and
#          prints accuracy; the photos it got wrong land in gs://$CNH_BUCKET/eval/<run>/.
# thumbs:  one pod per extracted talk makes a thumbnail and a slide title for each still (details.json).
# details: one pod loads those and gs://$CNH_BUCKET/derived/sched/$CNH_EVENT.json (ingest/sched.py) into
#          kubecon-details, which has no semantic fields: no inference, so Elastic's limits don't apply.
#
# Needs CNH_BUCKET, and kubectl on the cnh cluster: source the hackathon infra/config.env. Inside the
# cluster (the CI runners) kubectl uses the pod's service account instead.
# INGEST_NS picks the namespace (default ingest). CI uses ingest-ci, whose key and bucket access are restricted.
# The code ships as a ConfigMap from this checkout, so a code change needs no image build.
# BOOTSTRAP=1 installs ffmpeg and the OCR libraries at pod start on python:3.12-slim, for testing
# before the ingest image exists. Otherwise INGEST_IMAGE (default below) must already hold them.
set -euo pipefail
cd "$(dirname "$0")"

: "${CNH_BUCKET:?source the hackathon infra/config.env first}"
EVENT=${CNH_EVENT:-kubecon-na-2024}
DERIVED=${DERIVED:-v1}
IMAGE=${INGEST_IMAGE:-ghcr.io/naterd/hackathon-cnh/ingest:latest}
NS=${INGEST_NS:-ingest}
VERSION=$(git rev-parse --short HEAD 2>/dev/null || echo dev)

if [[ -z ${KUBERNETES_SERVICE_HOST:-} ]]; then
  ctx=$(kubectl config current-context)
  [[ $ctx == *_cnh ]] || { echo "kubectl context is $ctx, not the cnh cluster" >&2; exit 1; }
fi

cmd=${1:-}; shift || true
STEP=extract
[[ $cmd == logs || $cmd == wait ]] && [[ -n ${1:-} ]] && { STEP=$1; shift; }
LIMIT="" TALKS="" PARALLEL=96 FORCE=0 INDEX=kubecon-moments-$DERIVED ALIAS="" REPLACE=0 N=200 SEED=1 MIN_TALK1="" MODES="" UPLOAD_PX=""
[[ $cmd == eval ]] && INDEX=kubecon-moments
[[ $cmd == thumbs || $cmd == details ]] && INDEX=kubecon-details
while (( $# )); do
  case $1 in
    --limit)     LIMIT=$2; shift ;;
    --talks)     TALKS=$2; shift ;;
    --parallel)  PARALLEL=$2; shift ;;
    --force)     FORCE=1 ;;
    --index)     INDEX=$2; shift ;;
    --alias)     ALIAS=$2; shift ;;
    --replace)   REPLACE=1 ;;
    --n)         N=$2; shift ;;
    --seed)      SEED=$2; shift ;;
    --min-talk1) MIN_TALK1=$2; shift ;;
    --modes)     MODES=$2; shift ;;
    --upload-px) UPLOAD_PX=$2; shift ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
  shift
done

render() {  # template file, then KEY=value pairs
  local f=$1; shift
  local args=(-e "s|__NS__|$NS|g" -e "s|__VERSION__|$VERSION|g")
  for kv in "$@"; do args+=(-e "s|__${kv%%=*}__|${kv#*=}|g"); done
  sed "${args[@]}" "$f"
}

ship_code() {
  kubectl -n $NS create configmap "ingest-code" --from-file=extract.py --from-file=index.py \
    --from-file=telemetry.py --from-file=eval.py --from-file=details.py --from-file=../docs/search_example.py \
    --from-file=../docs/layout-kubecon-na-2024.png --from-file=requirements.txt \
    --dry-run=client -o yaml | kubectl apply -f - >/dev/null
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
    kubectl -n $NS create configmap "ingest-talks" --from-literal=talks.txt="$ids" \
      --dry-run=client -o yaml | kubectl apply -f - >/dev/null
    setup=""
    if [[ ${BOOTSTRAP:-0} == 1 ]]; then
      IMAGE=python:3.12-slim
      setup="apt-get update -qq \&\& apt-get install -y -qq --no-install-recommends ffmpeg libgl1 libglib2.0-0 >/dev/null \&\& pip install -q -r /code/requirements.txt \&\&"
    fi
    kubectl -n $NS delete job "ingest-extract" --ignore-not-found >/dev/null
    render k8s/extract-job.yaml COUNT="$count" PARALLEL="$PARALLEL" IMAGE="$IMAGE" SETUP="$setup" \
      EVENT="$EVENT" DERIVED="$DERIVED" BUCKET="$CNH_BUCKET" FORCE="$FORCE" | kubectl apply -f -
    echo "extracting $count talks, $PARALLEL at a time. Progress: ingest/run.sh status"
    ;;
  index)
    args=""
    [[ -n $ALIAS ]] && args+=" --alias $ALIAS"
    [[ -n $LIMIT ]] && args+=" --limit $LIMIT"
    [[ -n $TALKS ]] && args+=" --talks=$TALKS"  # = form: ids can start with "-"
    [[ $REPLACE == 1 ]] && args+=" --replace"
    ship_code
    kubectl -n $NS delete job "ingest-index" --ignore-not-found >/dev/null
    render k8s/index-job.yaml EVENT="$EVENT" DERIVED="$DERIVED" BUCKET="$CNH_BUCKET" \
      INDEX="$INDEX" ARGS="$args" | kubectl apply -f -
    echo "indexing into $INDEX. Follow it: ingest/run.sh logs index"
    ;;
  eval)
    run=$(date +%Y%m%d-%H%M%S)-$VERSION
    args=""
    [[ -n $MIN_TALK1 ]] && args+=" --min-talk1 $MIN_TALK1"
    [[ -n $MODES ]] && args+=" --modes $MODES"
    [[ -n $UPLOAD_PX ]] && args+=" --upload-px $UPLOAD_PX"
    ship_code
    kubectl -n $NS delete job "ingest-eval" --ignore-not-found >/dev/null
    render k8s/eval-job.yaml IMAGE="$IMAGE" EVENT="$EVENT" DERIVED="$DERIVED" BUCKET="$CNH_BUCKET" INDEX="$INDEX" \
      N="$N" SEED="$SEED" RUN="$run" ARGS="$args" | kubectl apply -f -
    echo "eval of $N photos against $INDEX (run $run). Follow it: ingest/run.sh logs eval"
    ;;
  thumbs|details)
    if [[ $cmd == details ]]; then
      count=1 ids=""
    elif [[ -n $TALKS ]]; then
      ids=$(tr ',' '\n' <<<"$TALKS")
    else
      ids=$(gcloud storage ls "gs://$CNH_BUCKET/derived/$DERIVED/$EVENT/*/segments.json" | awk -F/ '{print $(NF-1)}' | sort -u)
    fi
    [[ $cmd == thumbs ]] && count=$(grep -c . <<<"$ids") && (( PARALLEL > count )) && PARALLEL=$count
    [[ $cmd == details ]] && PARALLEL=1
    ship_code
    # its own talk list: a running extract Job reads ingest-talks by completion index
    kubectl -n $NS create configmap "ingest-talks-$cmd" --from-literal=talks.txt="$ids" \
      --dry-run=client -o yaml | kubectl apply -f - >/dev/null
    kubectl -n $NS delete job "ingest-$cmd" --ignore-not-found >/dev/null
    render k8s/details-job.yaml STEP="$cmd" COUNT="$count" PARALLEL="$PARALLEL" IMAGE="$IMAGE" EVENT="$EVENT" \
      DERIVED="$DERIVED" BUCKET="$CNH_BUCKET" FORCE="$FORCE" INDEX="$INDEX" | kubectl apply -f -
    echo "$cmd: $count pods, $PARALLEL at a time, into $INDEX. Follow it: ingest/run.sh logs $cmd"
    ;;
  wait)
    job=ingest-$STEP state=timeout
    for _ in $(seq 1 "${WAIT_TRIES:-180}"); do  # 10 s apart: 30 minutes
      conds=$(kubectl -n $NS get job "$job" -o jsonpath='{.status.conditions[?(@.status=="True")].type}')
      [[ $conds == *Complete* ]] && { state=ok; break; }
      [[ $conds == *Failed* ]] && { state=failed; break; }
      sleep 10
    done
    kubectl -n $NS logs -l "job-name=$job" -c "$STEP" --tail=60 --prefix --max-log-requests 20 || true
    echo "$job: $state"
    [[ $state == ok ]]
    ;;
  status)
    kubectl -n $NS get jobs -l app=ingest \
      -o custom-columns='JOB:.metadata.name,DONE:.status.succeeded,FAILED:.status.failed,ACTIVE:.status.active,TOTAL:.spec.completions' 2>/dev/null
    n=$(gcloud storage ls "gs://$CNH_BUCKET/derived/$DERIVED/$EVENT/*/segments.json" 2>/dev/null | grep -c . || true)
    echo "extracted talks in derived/$DERIVED: $n"
    ;;
  logs)
    kubectl -n $NS logs -l "job-name=ingest-$STEP" -c "$STEP" --tail=80 --prefix --max-log-requests 20
    ;;
  *)
    sed -n '2,27p' "$0"; exit 2 ;;
esac
