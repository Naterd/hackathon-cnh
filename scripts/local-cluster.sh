#!/usr/bin/env bash
# A Kubernetes cluster on your laptop, set up like the cluster the hackathon
# gives each team, for when the shared one has a problem. It runs this repo
# unchanged: same namespace, same Argo CD project, same argocd/application.yaml.
#
#   ./scripts/local-cluster.sh <team-name>          create it, safe to re-run
#   ./scripts/local-cluster.sh <team-name> --down   delete it
#   ./scripts/local-cluster.sh --pull               download everything now, on good wifi
#   ./scripts/local-cluster.sh --images             list what it downloads
#
# Needs Docker running (Docker Desktop, OrbStack, colima or Rancher Desktop)
# with about 4 GB of memory free, kubectl, helm, and one of k3d (preferred),
# kind or minikube. Set CNH_CLUSTER_TOOL=kind (or minikube) to pick one.
set -euo pipefail

# Same chart versions and settings as the hackathon's team clusters
# (skyhook-dev/cnh-hackathon-infra: config.env, platform/values/vcluster-*.yaml).
K3S_IMAGE="rancher/k3s:v1.33.6-k3s1"
ARGOCD_CHART_VERSION="10.11.7"          # Argo CD v3.5.7
ROLLOUTS_CHART_VERSION="2.43.8"         # Argo Rollouts v1.10.1
GATEWAY_API_VERSION="v1.6.3"            # CRDs only, so deploy/httproute.yaml applies
METRICS_SERVER_CHART_VERSION="3.13.0"   # kind only: k3d and minikube bring their own

ARGO_HELM="https://github.com/argoproj/argo-helm/releases/download"
DOWNLOADS=(
  "$ARGO_HELM/argo-cd-$ARGOCD_CHART_VERSION/argo-cd-$ARGOCD_CHART_VERSION.tgz"
  "$ARGO_HELM/argo-rollouts-$ROLLOUTS_CHART_VERSION/argo-rollouts-$ROLLOUTS_CHART_VERSION.tgz"
  "https://github.com/kubernetes-sigs/gateway-api/releases/download/$GATEWAY_API_VERSION/standard-install.yaml"
  "https://github.com/kubernetes-sigs/metrics-server/releases/download/metrics-server-helm-chart-$METRICS_SERVER_CHART_VERSION/metrics-server-$METRICS_SERVER_CHART_VERSION.tgz"
)
# What the cluster pulls. The k3s ones are what k3s itself runs (traefik and
# servicelb are turned off); kind and minikube bring theirs in the node image.
# Radar's is what the Connect a cluster command installs today.
IMAGES_K3S=(
  "$K3S_IMAGE"
  rancher/mirrored-pause:3.6
  rancher/mirrored-coredns-coredns:1.13.1
  rancher/local-path-provisioner:v0.0.32
  rancher/mirrored-metrics-server:v0.8.0
)
IMAGES=(
  quay.io/argoproj/argocd:v3.5.7
  ecr-public.aws.com/docker/library/redis:8.6.4-alpine
  quay.io/argoproj/argo-rollouts:v1.10.1
  ghcr.io/skyhook-io/radar:1.19.0
)
IMAGES_KIND=(registry.k8s.io/metrics-server/metrics-server:v0.8.0)
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/cnh-local"

say()  { printf '\n==> %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
die()  { printf '\nerror: %s\n' "$*" >&2; exit 1; }
interactive() { [[ -t 0 && -t 1 ]]; }
usage() {
  sed -n '6,9p' "$0" | sed 's/^# //' >&2
  exit 1
}

team="" mode=up
for arg in "$@"; do
  case "$arg" in
    --down) mode=down ;;
    --pull) mode=pull ;;
    --images) mode=images ;;
    -h|--help) usage ;;
    -*) usage ;;
    *) [[ -z "$team" ]] || usage; team="$arg" ;;
  esac
done
if [[ "$mode" == up || "$mode" == down ]]; then
  [[ "$team" =~ ^[a-z0-9]([a-z0-9-]{0,18}[a-z0-9])?$ ]] ||
    { echo "usage: $0 <team-name> [--down]   (lowercase letters, digits and dashes, up to 20 characters)" >&2; exit 1; }
fi
name="cnh-$team" ns="team-$team"

# --- tools -------------------------------------------------------------------

install_hint() { # <tool>
  if [[ "$(uname -s)" == Darwin ]]; then
    echo "brew install $1"
  elif [[ "$1" == k3d ]]; then
    echo "curl -s https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh | bash"
  else
    echo "see https://kubernetes.io/docs/tasks/tools/ (kubectl) or https://helm.sh/docs/intro/install/ (helm)"
  fi
}

need() { # <command>
  command -v "$1" >/dev/null 2>&1 || die "$1 isn't installed. Install it with: $(install_hint "$1")"
}

need_docker() {
  command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1 ||
    die "Docker isn't running. Start Docker Desktop, OrbStack, colima or Rancher Desktop (or install one), then run this again."
  local mem
  mem="$(docker info -f '{{.MemTotal}}' 2>/dev/null || echo 0)"
  if (( mem > 0 && mem < 4 * 1024 * 1024 * 1024 )); then
    info "Docker has only $(( mem / 1024 / 1024 ))MiB of memory; the cluster and your app need 2 to 3 GiB. Give Docker more if pods won't start."
  fi
}

free_port() { # a localhost port nothing listens on, from 6550 up
  local p
  for p in $(seq 6550 6650); do
    (exec 3<>"/dev/tcp/127.0.0.1/$p") 2>/dev/null || { echo "$p"; return; }
  done
  die "No free port between 6550 and 6650 for the cluster's API."
}

cluster_exists() { # <tool>
  case "$1" in
    k3d) k3d cluster get "$name" >/dev/null 2>&1 ;;
    kind) [[ $'\n'"$(kind get clusters 2>/dev/null || true)"$'\n' == *$'\n'"$name"$'\n'* ]] ;;
    minikube) [[ "$(minikube profile list -o json 2>/dev/null || true)" == *"\"Name\":\"$name\""* ]] ;;
  esac
}

# The tool that already has this cluster, else the first one installed.
pick_tool() {
  local t
  if [[ -n "${CNH_CLUSTER_TOOL:-}" ]]; then
    case "$CNH_CLUSTER_TOOL" in k3d|kind|minikube) ;; *) die "CNH_CLUSTER_TOOL must be k3d, kind or minikube" ;; esac
    need "$CNH_CLUSTER_TOOL"; tool="$CNH_CLUSTER_TOOL"; return
  fi
  for t in k3d kind minikube; do
    if command -v "$t" >/dev/null 2>&1 && cluster_exists "$t"; then tool="$t"; return; fi
  done
  for t in k3d kind minikube; do
    if command -v "$t" >/dev/null 2>&1; then tool="$t"; return; fi
  done
  die "No local Kubernetes tool found. Install k3d (light, quick to start) with: $(install_hint k3d)"
}

# A chart or manifest, from the cache when --pull already downloaded it.
fetch() { # <url>: prints the local path
  local f="$CACHE/${1##*/}"
  [[ "$1" == *standard-install.yaml ]] && f="$CACHE/gateway-api-$GATEWAY_API_VERSION-standard-install.yaml"
  if [[ ! -s "$f" ]]; then
    mkdir -p "$CACHE"
    curl -fsSL --retry 3 -o "$f.tmp" "$1" || die "Couldn't download $1"
    mv "$f.tmp" "$f"
  fi
  printf '%s\n' "$f"
}

images_for() { # <tool>
  if [[ "$1" == k3d ]]; then
    printf '%s\n' "${IMAGES_K3S[@]}"
    # k3d runs its tools image, of its own version, while it creates a cluster.
    k3d version 2>/dev/null | sed -n 's/^k3d version v\(.*\)$/ghcr.io\/k3d-io\/k3d-tools:\1/p'
  fi
  printf '%s\n' "${IMAGES[@]}"
  [[ "$1" == kind ]] && printf '%s\n' "${IMAGES_KIND[@]}"
  return 0
}

# --- --images / --pull -------------------------------------------------------

if [[ "$mode" == images || "$mode" == pull ]]; then
  pick_tool
  say "Images for a $tool cluster"
  images_for "$tool" | sed 's/^/    /'
  [[ "$tool" == k3d ]] || info "plus the $tool node image, which $tool picks itself."
  info "Your team's own image (ghcr.io/<you>/<repo>) comes down when Argo CD deploys it."
  say "Charts and manifests"
  printf '    %s\n' "${DOWNLOADS[@]}"
  [[ "$mode" == images ]] && exit 0

  need_docker
  say "Downloading (about 500 MB)"
  for img in $(images_for "$tool"); do
    info "$img"
    docker pull -q "$img" >/dev/null || die "Couldn't pull $img"
  done
  for url in "${DOWNLOADS[@]}"; do fetch "$url" >/dev/null; done
  info "charts and manifests in $CACHE"
  say "Done. On the day, ./scripts/local-cluster.sh <team-name> copies these into the cluster instead of downloading them."
  exit 0
fi

# --- --down ------------------------------------------------------------------

if [[ "$mode" == down ]]; then
  need_docker
  for t in k3d kind minikube; do
    command -v "$t" >/dev/null 2>&1 && cluster_exists "$t" || continue
    say "Deleting $t cluster $name"
    case "$t" in
      k3d) k3d cluster delete "$name" ;;
      kind) kind delete cluster --name "$name" ;;
      minikube) minikube delete -p "$name" ;;
    esac
    say "Deleted. If you connected it to Radar Cloud, it stays there as disconnected: remove it under Clusters."
    exit 0
  done
  say "No cluster named $name. Nothing to delete."
  exit 0
fi

# --- 1. Docker and tools -----------------------------------------------------

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
need_docker
need kubectl
need helm
need git
pick_tool

# --- 2. The cluster ----------------------------------------------------------

created=""
case "$tool" in
  k3d)
    ctx="k3d-$name"
    if cluster_exists k3d; then
      say "Cluster $name exists (k3d); making sure it's running"
      k3d cluster start "$name" --wait >/dev/null
      k3d kubeconfig merge "$name" --kubeconfig-merge-default --kubeconfig-switch-context >/dev/null
    else
      say "Creating cluster $name with k3d"
      # The API listens on localhost only: venue wifi is a shared network.
      k3d cluster create "$name" --image "$K3S_IMAGE" --servers 1 --agents 0 --no-lb \
        --api-port "127.0.0.1:$(free_port)" \
        --k3s-arg '--disable=traefik@server:0' --k3s-arg '--disable=servicelb@server:0' \
        --wait --timeout 3m
      created=1
    fi
    ;;
  kind)
    ctx="kind-$name"
    if cluster_exists kind; then
      say "Cluster $name exists (kind); making sure it's running"
      docker start "$name-control-plane" >/dev/null
      kind export kubeconfig --name "$name" >/dev/null
    else
      say "Creating cluster $name with kind"
      kind create cluster --name "$name" --wait 3m
      created=1
    fi
    ;;
  minikube)
    ctx="$name"
    cluster_exists minikube || created=1
    say "Starting cluster $name with minikube"
    minikube start -p "$name" --driver=docker --cpus=2 --memory=4096
    ;;
esac
kubectl config use-context "$ctx" >/dev/null
info "kubectl now uses context $ctx"
k() { kubectl --context "$ctx" "$@"; }
h() { helm --kube-context "$ctx" "$@"; }

# Images --pull downloaded, copied in instead of downloaded again.
if [[ -n "$created" ]]; then
  have=()
  for img in $(images_for "$tool"); do
    [[ "$img" == "$K3S_IMAGE" || "$img" == *k3d-tools* ]] && continue
    docker image inspect "$img" >/dev/null 2>&1 && have+=("$img")
  done
  if (( ${#have[@]} )); then
    say "Copying ${#have[@]} downloaded images into the cluster"
    # Only this machine's platform. With Docker's containerd image store, a
    # multi-platform image saved whole names layers that were never pulled,
    # and the cluster's import fails on them.
    save=(docker save -o "$tmp/images.tar")
    plat="$(docker version -f '{{.Server.Os}}/{{.Server.Arch}}' 2>/dev/null || true)"
    if [[ -n "$plat" && "$(docker save --help 2>/dev/null || true)" == *--platform* ]]; then
      save+=(--platform "$plat")
    fi
    if "${save[@]}" "${have[@]}" >/dev/null 2>&1 &&
       case "$tool" in
         k3d) k3d image import -m direct -c "$name" "$tmp/images.tar" ;;
         kind) kind load image-archive "$tmp/images.tar" --name "$name" ;;
         minikube) minikube -p "$name" image load "$tmp/images.tar" ;;
       esac >/dev/null 2>&1; then
      info "done"
    else
      info "That didn't work; the cluster downloads them itself."
    fi
    rm -f "$tmp/images.tar"
  fi
fi

# --- 3. What the hackathon's clusters come with -----------------------------

say "Installing Argo CD, Argo Rollouts and the Gateway API CRDs"

if ! k get crd httproutes.gateway.networking.k8s.io >/dev/null 2>&1; then
  k apply --server-side -f "$(fetch "${DOWNLOADS[2]}")" >/dev/null
fi
case "$tool" in
  kind) need_metrics=1 ;;
  minikube) minikube -p "$name" addons enable metrics-server >/dev/null; need_metrics="" ;;
  *) need_metrics="" ;;
esac

installed() { # <release> <namespace> <chart>-<version>
  local out
  out="$(h list -n "$2" --filter "^$1\$" -o json 2>/dev/null)" || return 1
  [[ "$out" == *"\"status\":\"deployed\""* && "$out" == *"\"chart\":\"$3\""* ]]
}

# Argo CD core: the controller, repo server and redis, no UI. Same values as
# a team's vcluster, so Applications in team-* namespaces work.
argocd_values() {
  cat <<'EOF'
crds:
  install: true
server:
  enabled: false
applicationSet:
  replicas: 0
dex:
  enabled: false
notifications:
  enabled: false
configs:
  params:
    application.namespaces: "team-*"
  cm:
    timeout.reconciliation: 60s
    # deploy/httproute.yaml has no Gateway here, so it never gets a status.
    resource.customizations.health.gateway.networking.k8s.io_HTTPRoute: |
      hs = {}
      hs.status = "Healthy"
      hs.message = "Routed by the hackathon platform"
      return hs
controller:
  resources:
    requests: { cpu: 100m, memory: 256Mi }
    limits: { cpu: 500m, memory: 1Gi }
repoServer:
  resources:
    requests: { cpu: 50m, memory: 128Mi }
    limits: { cpu: 500m, memory: 512Mi }
redis:
  resources:
    requests: { cpu: 20m, memory: 32Mi }
    limits: { cpu: 100m, memory: 128Mi }
EOF
}
rollouts_values() {
  cat <<'EOF'
controller:
  replicas: 1
  resources:
    requests: { cpu: 50m, memory: 128Mi }
    limits: { cpu: 200m, memory: 256Mi }
dashboard:
  enabled: false
EOF
}

pids=()
if ! installed argocd argocd "argo-cd-$ARGOCD_CHART_VERSION"; then
  info "Argo CD $ARGOCD_CHART_VERSION"
  chart="$(fetch "${DOWNLOADS[0]}")"
  h upgrade --install argocd "$chart" -n argocd --create-namespace \
    -f <(argocd_values) --wait --timeout 6m >"$tmp/argocd" 2>&1 &
  pids+=("$!:argocd")
fi
if ! installed argo-rollouts argo-rollouts "argo-rollouts-$ROLLOUTS_CHART_VERSION"; then
  info "Argo Rollouts $ROLLOUTS_CHART_VERSION"
  chart="$(fetch "${DOWNLOADS[1]}")"
  h upgrade --install argo-rollouts "$chart" -n argo-rollouts --create-namespace \
    -f <(rollouts_values) --wait --timeout 6m >"$tmp/argo-rollouts" 2>&1 &
  pids+=("$!:argo-rollouts")
fi
if [[ -n "$need_metrics" ]] && ! installed metrics-server kube-system "metrics-server-$METRICS_SERVER_CHART_VERSION"; then
  info "metrics-server $METRICS_SERVER_CHART_VERSION"
  chart="$(fetch "${DOWNLOADS[3]}")"
  # kind's kubelets use self-signed certificates.
  h upgrade --install metrics-server "$chart" -n kube-system \
    --set 'args={--kubelet-insecure-tls}' --wait --timeout 6m >"$tmp/metrics-server" 2>&1 &
  pids+=("$!:metrics-server")
fi
failed=""
for p in ${pids[@]+"${pids[@]}"}; do
  wait "${p%%:*}" || { failed=1; printf '\n%s failed:\n' "${p#*:}" >&2; cat "$tmp/${p#*:}" >&2; }
done
[[ -z "$failed" ]] || die "Install failed (above). Run this again; it picks up where it stopped."
# ApplicationSets are off; their Service would only show in Radar's Issues as "No endpoints".
k -n argocd delete service argocd-applicationset-controller --ignore-not-found >/dev/null

# The team's namespace and Argo CD projects, as on the hackathon's clusters.
# The namespace exists up front because Radar 1.19's manage_gitops sync drops
# an Application's own syncOptions, CreateNamespace=true included.
k apply -f - >/dev/null <<EOF
apiVersion: v1
kind: Namespace
metadata:
  name: $ns
---
apiVersion: argoproj.io/v1alpha1
kind: AppProject
metadata: { name: $ns, namespace: argocd }
spec:
  description: "Hackathon team $team"
  sourceRepos: ["*"]
  sourceNamespaces: ["$ns"]
  destinations: [{ server: https://kubernetes.default.svc, namespace: "*" }]
  clusterResourceWhitelist: [{ group: "*", kind: "*" }]
---
apiVersion: argoproj.io/v1alpha1
kind: AppProject
metadata: { name: default, namespace: argocd }
spec:
  sourceRepos: ["*"]
  sourceNamespaces: ["*"]
  destinations: [{ server: "*", namespace: "*" }]
  clusterResourceWhitelist: [{ group: "*", kind: "*" }]
EOF
info "namespace $ns and Argo CD project $ns are ready"

# --- 4. Radar ----------------------------------------------------------------

# Pauses at a step only the person can do. Without a terminal to ask in, it
# stops here; running the script again picks up from this step.
wait_for_person() { # <prompt>
  if ! interactive; then
    say "Run ./scripts/local-cluster.sh $team again when that's done. It picks up from here."
    exit 0
  fi
  printf '\n    %s ' "$1"
  read -r answer || answer=""
}

radar_deploy() { # prints <namespace>/<name> of Radar's Deployment, or nothing
  k get deploy -A -l app.kubernetes.io/name=radar --no-headers \
    -o custom-columns=NS:.metadata.namespace,NAME:.metadata.name 2>/dev/null | awk 'NR == 1 { print $1 "/" $2 }' || true
}

if [[ -z "$(radar_deploy)" ]]; then
  say "Connect this cluster to Radar Cloud, in your own free organization, not Cloud Native Hackathon"
  info "1. Sign in at https://app.radarhq.io. New to Radar Cloud: name your organization when it asks."
  info "   Already in Cloud Native Hackathon: open the menu under your name (bottom left) >"
  info "   Create organization. Invite your teammates from Settings > Members, and check that the"
  info "   organization switcher shows your new organization."
  info "2. Click Connect a cluster, name the cluster exactly $team, and copy the Helm command it shows."
  info "3. Paste it into a terminal on this laptop. kubectl's context is $ctx, so it installs"
  info "   Radar here (check: kubectl config current-context)."
  info "The command carries your cluster's token. Paste it straight into your terminal:"
  info "never into this repo, a chat, or your coding agent."
  while [[ -z "$(radar_deploy)" ]]; do
    wait_for_person "Press Enter once the Helm command has finished (or type skip):"
    [[ "$answer" == skip ]] && break
    [[ -n "$(radar_deploy)" ]] || info "Radar isn't in $ctx yet. Did the command run with another kubectl context?"
  done
fi
if [[ -n "$(radar_deploy)" ]]; then
  rd="$(radar_deploy)"
  info "Waiting for Radar to be ready"
  if k -n "${rd%%/*}" rollout status "deploy/${rd#*/}" --timeout 3m >/dev/null 2>&1; then
    info "Radar is running. Open the cluster in Radar Cloud; it shows Connected within a minute."
  else
    info "Radar isn't ready after 3 minutes. Look at its pod: kubectl -n ${rd%%/*} get pods"
  fi
fi

# --- 5. Your repo and Argo CD ------------------------------------------------

cd "$(dirname "$0")/.."
git rev-parse --git-dir >/dev/null 2>&1 || die "Run this from your clone of the starter repo."
remote="$(git remote get-url origin 2>/dev/null || true)"
repo="$(printf '%s' "$remote" | sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##')"
if [[ "$(printf '%s' "$repo" | tr '[:upper:]' '[:lower:]')" == skyhook-io/cnh-starter ]]; then
  die "This is the template itself. On GitHub, click Use this template > Create a new repository (public), clone your copy, and run this script inside it. The cluster is ready and stays."
fi

if grep -q __TEAM__ deploy/kustomization.yaml; then
  say "Pointing this repo at team $team"
  ./scripts/setup.sh "$team" >/dev/null
fi
repo_ns="$(sed -n 's/^namespace: *//p' deploy/kustomization.yaml)"
[[ "$repo_ns" == "$ns" ]] ||
  die "This repo is set up for team ${repo_ns#team-}, not $team. Run: ./scripts/local-cluster.sh ${repo_ns#team-} (and $0 $team --down to drop this cluster)."

# Argo CD deploys what's on GitHub, so the setup has to be pushed first.
pushed() {
  git fetch -q origin main 2>/dev/null || return 1
  local kz
  kz="$(git show origin/main:deploy/kustomization.yaml 2>/dev/null)" || return 1
  [[ "$kz" == *"namespace: $ns"* && "$kz" != *"ghcr.io/OWNER/REPO"* ]]
}
if ! k -n "$ns" get applications.argoproj.io app >/dev/null 2>&1; then
  until pushed; do
    say "Push your repo, so Argo CD can deploy it"
    if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
      info "git commit -am \"Set up team $team\" && git pull --rebase && git push"
    else
      info "git pull --rebase && git push"
    fi
    info "Then wait for the GitHub Action to go green (your repo's Actions tab), and make the image"
    info "public once: on your repo's GitHub page, Packages > Package settings > Change visibility > Public."
    wait_for_person "Press Enter once the Action is green:"
  done
  say "Starting GitOps: argocd/application.yaml"
  k apply -f argocd/application.yaml >/dev/null
fi

info "Waiting for your app (up to 3 minutes)"
hinted_image="" hinted_repo=""
for _ in $(seq 36); do
  ready="$(k -n "$ns" get deploy app -o jsonpath='{.status.availableReplicas}' 2>/dev/null || true)"
  (( ${ready:-0} > 0 )) && break
  waiting="$(k -n "$ns" get pods -l app=app -o jsonpath='{.items[*].status.containerStatuses[*].state.waiting.reason}' 2>/dev/null || true)"
  if [[ "$waiting" == *ImagePull* && -z "$hinted_image" ]]; then
    info "The cluster can't pull your image yet. Check that the Action is green and the package is"
    info "Public (your repo's GitHub page > Packages > Package settings > Change visibility)."
    hinted_image=1
  fi
  conditions="$(k -n "$ns" get applications.argoproj.io app -o jsonpath='{.status.conditions[*].message}' 2>/dev/null || true)"
  # A private or missing repo: git says one or the other, depending on the repo.
  if [[ ( "$conditions" == *"authentication required"* || "$conditions" == *"could not read Username"* ) &&
        -z "$hinted_repo" ]]; then
    info "Argo CD can't read $repo on GitHub. Make the repo public (Settings > General >"
    info "Danger Zone > Change visibility), or check the repoURL in argocd/application.yaml."
    hinted_repo=1
  fi
  sleep 5
done
if (( ${ready:-0} > 0 )); then
  info "Your app is running in $ns."
else
  info "Not ready yet; Argo CD keeps trying. In Radar Cloud, open the Application app in $ns to see why."
fi

# --- 6. How to use it --------------------------------------------------------

cat <<EOF

==> Done: cluster $name (kubectl context $ctx)

    Open your app. There's no public URL on a laptop cluster, so forward a port:
      kubectl -n $ns port-forward svc/app 8080:80
    and open http://localhost:8080.

    Connect your coding agent to Radar's MCP server. In Radar Cloud, open Agents and pick your
    client; under "Organization access" pick your own organization, not Cloud Native Hackathon.
    For Claude Code:
      claude mcp add radar-cloud --transport http https://api.radarhq.io/mcp

    AGENTS.md describes the hackathon's cluster: add the note from the README's "On your laptop"
    section to the top of it. Either way, start your agent with:
      We're on a local cluster in our own Radar Cloud organization, named $team. AGENTS.md's
      parts about the hackathon organization, Prometheus, the budget, the host's limits and the
      public URL don't apply here; reach the app with kubectl port-forward. Read AGENTS.md, find
      our cluster in Radar, and tell me what's running in $ns. Don't change anything yet.

    Delete the cluster when you're done: ./scripts/local-cluster.sh $team --down
EOF
