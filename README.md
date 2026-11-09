# Radar AI SRE track starter: Cloud Native Hackathon

Start here if your team has its own cluster at the hackathon: a virtual Kubernetes cluster (vcluster) where you're cluster-admin. You get a small app that `git push` deploys through Argo CD, and notes your coding agent reads so it ships through [Radar](https://radarhq.io)'s MCP server. Below is a menu of what Radar can do: pick what your project needs.

## The day

- 11:00: before you write any code, get your cluster, open it in Radar Cloud, and connect your agent to Radar (steps below).
- First hour: open **Checks** for your cluster and take a screenshot. That's your "before".
- All day, work through Radar: your agent pushes, syncs and waits for each rollout through Radar's MCP. When something breaks, look in Radar first, and start an AI investigation on anything you can't explain. Run at least one investigation today (the judges look for it). Nothing broke? Investigate your app after a release: it ends with an all-clear and the evidence, which counts.
- By 5:30: tell the day's story from Radar: the timeline, the investigation, and Checks before and after. It's only there if you used Radar all day. Your agent can draft it: ask it for your 5:30 story.
- The winning team gets $4,000. Hackathon clusters and access to the hackathon org end on Friday, November 13.

## Before you start

Get your team's cluster at **join.cnh.radarhq.io** (the join code is shared at the kickoff and on the Radar table). It takes 3 to 4 minutes; `join.cnh.radarhq.io/status/<team-name>` shows the progress and says when it's ready. Then you have:

- your own cluster, named `<team-name>` in Radar Cloud,
- this starter app already running in it, in namespace `team-<team-name>`, at `https://<team-name>.cnh.radarhq.io`,
- an invite for each of you to the hackathon's organization in Radar Cloud. Accept it and sign in at [app.radarhq.io](https://app.radarhq.io).

**Joining a team that already started?** Don't fill in the join form or copy the template again: ask your team for the repo, and ask us at the Radar table to add your email.

Every team is in the same Radar Cloud organization, so Radar Cloud lists every team's cluster, each under its team's name, plus a cluster named `cnh`, the platform's own cluster. You only have access to your own team's cluster. The member list (with emails), the Alerts page and the bell (each alert's resource, namespace and status), and any investigation you mark **Shared** are visible to every team. Investigations start private: only the person who started one can open it. So that person shows it at 5:30, or shares it on purpose, and then every team can read it. Alert notifications are off for the event, because they would go to everyone.

## Connect your agent

For Claude Code, run this once, from any folder:

```bash
claude mcp add --scope user radar-cloud --transport http https://api.radarhq.io/mcp
```

Then, in Claude Code, run `/mcp`, pick **radar-cloud** and **Authenticate**: it opens your browser. Under "Organization access", pick **Cloud Native Hackathon**. Another agent? In Radar Cloud, open **Agents** and pick yours.

Start your agent in your copy of this repo (below) and give it a first prompt:

> Get me set up for the hackathon. Our team is <team-name>.

Every Radar MCP tool takes a `cluster` id, and your agent must pass yours on every call: the organization has about 40 clusters. AGENTS.md tells it how to find the id and how to ship.

This repo also has skills for getting set up, shipping, debugging, canaries and the 5:30 story in `.agents/skills/`. Claude Code and Codex pick them up on their own.

## Ship your own code

1. **Make your copy.** Click **Use this template** > **Create a new repository**. Make it **public**, because Argo CD in your cluster reads it without credentials. Clone it.
2. **Point it at your team.**
   ```bash
   ./scripts/setup.sh <team-name>
   git commit -am "Set up team <team-name>" && git pull --rebase && git push
   ```
   The `git pull` picks up a commit the Action may already have made when GitHub created your repo. The Action builds the image and pushes it to GHCR. Wait for it to go green (the repo's **Actions** tab).
3. **Check the image is public.** On your repo's GitHub page, open **Packages** (right-hand column) and your image: it should say **Public**, and usually already does. If it says **Private**, open **Package settings** > **Change visibility** > **Public**, because your cluster can't pull a private image.
4. **Start GitOps.** Ask your agent to apply `argocd/application.yaml` to your cluster through Radar's MCP server. Argo CD takes over the starter app and from then on deploys every push to `main`.
5. **Check it's yours.** Open `https://<team-name>.cnh.radarhq.io`. It shows `version` and the first 7 characters of the commit you pushed: the Action tags the image with it. The bot's `deploy:` commit after yours has its own SHA; ignore that one.

One person can do this while the others accept their invites.

## API keys and other secrets

Your repo and your image are public, so an API key for a hosted AI model, or any other secret, must never be in either. Keep it in a Kubernetes Secret in your cluster, and give it to your app as an environment variable:

1. **Create the Secret.** In Radar Cloud, open your cluster, then **Resources** > **Secrets** (under Configuration), and click **+** (Create Secret). In the YAML it shows, set `name: app-credentials` and `namespace: team-<team-name>`, replace `key: value` under `stringData` with `api-key: <your key>`, and click **Apply**. Or, with [kubectl](#kubectl):
   ```bash
   read -rs API_KEY   # paste the key, then press Enter
   kubectl -n team-<team-name> create secret generic app-credentials --from-literal=api-key="$API_KEY"
   ```
   Create it yourself rather than giving the key to your agent: whatever you paste into a chat stays in its history.
2. **Read it in your app.** Add this to the `env:` list of the container in `deploy/deployment.yaml`, then push. Only the Secret's name goes in git, never the key:
   ```yaml
   - name: API_KEY
     valueFrom:
       secretKeyRef:
         name: app-credentials
         key: api-key
   ```
   If you change the key later, restart the app so it reads the new value.

Argo CD leaves this Secret alone, even with `prune: true`: it only prunes what it deployed from git. Never put a key in the code, the Dockerfile, a build argument or a commit, and don't log it. If one does reach GitHub or your image, revoke it with the provider and make a new one. Your agent's recipe for this, plus one-off Jobs, dependencies and checking the running app, is in [`examples/agent-workflows`](examples/agent-workflows/README.md).

## What Radar can do for you

Pick what your project needs. None of it is required, and more doesn't score higher.

| You want to | In Radar Cloud | Or ask your agent |
|---|---|---|
| See what's running and how it connects | Topology, Resources | "Show me what our app talks to and depends on." |
| Ship every push and know it worked | GitOps, the Application's page | "Ship this, and tell me when it's healthy and live." |
| Find what's broken right now | Issues, the workload's Timeline and Logs | "What's unhealthy in our cluster, and why?" |
| See what changed | Timeline, with diffs | "What changed in the last hour?" |
| Trace a failure to its cause | **Investigate** on the failing resource | Request a hosted investigation when available; otherwise start it in Radar Cloud. |
| Check health and security | Checks | "Review Checks for our namespace and fix what applies." |
| See load and resource use over time | The workload's Metrics tab | "Is our memory climbing? Are our requests sized right?" |
| Add a database | Helm, Resources | "Create a small Postgres for us" (`examples/postgres`). |
| Restart, scale or run a job | The workload's page | "Restart the app." "Run the cronjob now." |
| Release carefully, or autoscale | The Rollout's page: Promote, Abort | "Switch us to canary releases" (`examples/canary`), or "Add autoscaling" (`examples/hpa`). |

**Going further:** Radar also understands OpenTelemetry, Sealed Secrets, Argo Workflows and Argo Rollouts analysis, among others. You're cluster-admin, so install what your project needs (see [Your cluster](#your-cluster)).

For credentials, one-off Jobs, dependencies and checking the running app, see [the optional agent workflows](examples/agent-workflows/README.md).

Replace `app/` with your own project whenever you like. Keep it listening on `$PORT` (8080) with `/healthz` and `/readyz`, or update the probes in `deploy/`. The Dockerfile installs the dependencies in `app/package.json`; for another language, change the Dockerfile too.

## What the judges look for

1. Run your project on Kubernetes with Radar connected, in the cluster we provide your team or on a cluster you bring.
2. Use Radar's UI and MCP throughout building and operating your project. Show how Radar helped you ship, troubleshoot, or improve your app.
3. Use Radar's AI investigations to investigate a real issue or question about your running app. Show what you learned and what you did with the findings.
4. Build something that makes conferences easier.

One real decision Radar helped you make is enough. More features don't score higher, and don't break things on purpose for the demo. Official wording: [hackathon page](https://www.wemakedevs.org/hackathons/cnh).

## Your cluster

Already installed: Argo CD in namespace `argocd`, Argo Rollouts, the Gateway API CRDs, metrics for `kubectl top` and the HPA, Prometheus in namespace `monitoring`, the CloudNativePG operator in `cnpg-system`, and Radar in `radar` (don't upgrade it). Argo CD has no web UI here: see and sync your Applications in Radar Cloud, or through your agent. It picks up Applications in `team-*` namespaces, and your Argo CD project is `team-<team-name>`.

**Metrics history.** Prometheus (`monitoring/prometheus-server`) scrapes your pods' CPU, memory and network, the kubelet, kube-state-metrics, and any pod or Service annotated `prometheus.io/scrape: "true"` (plus `prometheus.io/port` and `prometheus.io/path` if it isn't `/metrics`), every 30 seconds, and keeps 7 days. Radar finds it on its own: workloads get metric charts in Radar Cloud, and your agent can call `discover_metrics` to see what's there and `query_prometheus` to run PromQL. It doesn't count against your budget. It sees only your cluster. There's no node-exporter (the host refuses it) and no Alertmanager.

**Postgres.** The CloudNativePG operator is already running, so a database is one `Cluster`. Keep it in git next to your app so Argo CD applies it, or have your agent apply it with Radar's `apply_resource`:

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: db
  namespace: team-<team-name>
spec:
  instances: 1
  storage:
    size: 1Gi
```

It's ready in about a minute. Your app connects to `db-rw:5432`; the user, password and a full `uri` are in Secret `db-app`. Each instance is a pod and a PVC from your budget. `examples/postgres` has a version with requests and limits set, and the steps to wire it into your app.

**Your URL.** The platform routes `https://<team-name>.cnh.radarhq.io` to Service `app`, port 80, in namespace `team-<team-name>`. Keep a Service by that name and port there, whatever runs behind it. You can't get other hostnames, and Services of type LoadBalancer or NodePort aren't available. The HTTPRoute in `deploy/` does nothing inside your cluster (there's no Gateway in it), and Argo CD counts it healthy, so leave it.

**Install what you need.** You're cluster-admin, so CRDs, operators, Helm charts and more namespaces are all fine. For example, with [kubectl access](#kubectl), NATS for messaging:

```bash
helm repo add nats https://nats-io.github.io/k8s/helm/charts/
helm repo update nats
helm search repo nats/nats --versions
# Choose an exact version from the results, then replace CHART_VERSION below.
helm upgrade --install nats nats/nats --version CHART_VERSION --namespace nats --create-namespace
```

Or ask your agent to apply an operator's manifest with Radar's `apply_resource`, or to keep it in git as another Argo CD Application in `team-<team-name>`: your project `team-<team-name>` takes any source repo, any namespace in your cluster, and cluster-scoped resources.

**What the host refuses.** Privileged pods, and pods that use hostPath, hostNetwork or hostPID. Such a pod stays `Pending`, and its reason is a Warning event `SyncError` on the pod: you see it in Radar's events, and your agent through `get_events` or `diagnose`. Charts for node agents (log collectors, node exporters, storage drivers) often ask for these; turn that part off or pick another chart.

**Budget.** One total for everything you run, in any namespace, including what you install: 2 CPU and 4 GiB of requests, 4 CPU and 8 GiB of limits, and 4 PVCs with 20 GiB of storage (the default StorageClass is gp3). Containers without their own requests and limits get 100m CPU / 128Mi of requests and 500m / 512Mi of limits, so charts that set none still run. A pod that would go over stays `Pending` with a `SyncError` event that says `exceeded quota: team-quota`.

## kubectl

Radar is the main way in, but kubectl and helm work too. Your cluster's kubeconfig is in Secret `kubeconfig` in namespace `default`, key `config`:

1. In Radar Cloud, open your cluster, then **Resources** > **Secrets** (under Configuration), and open `kubeconfig` in namespace `default`.
2. Under **Data**, click **Reveal** next to `config`, then the copy icon that appears beside it ("Copy value"). Don't do this while sharing your screen.
3. Save it to a file outside your repo (the repo is public, and anyone with this file is cluster-admin in your cluster), and use it:
   ```bash
   export KUBECONFIG=~/cnh-kubeconfig
   kubectl get pods -n team-<team-name>
   ```

It talks to `https://<team-name>.vc.cnh.radarhq.io`. Its token lasts 24 hours and the platform replaces it about every 12 hours, so a fresh copy is good for at least 12 hours. If kubectl says `Unauthorized`, copy it again.

## When something's wrong

- **Your agent can't find your cluster.** Check that you accepted the invite with an email your team listed when it joined, and that you picked **Cloud Native Hackathon** under "Organization access" when you connected the MCP server. Your cluster is named exactly `<team-name>`.
- **A pod stays `Pending`.** Read its events in Radar. A `SyncError` means the host refused it (see [Your cluster](#your-cluster)): `exceeded quota: team-quota` means you're over your budget; otherwise it asks for privileged or host access.
- **The URL answers 404 or 503 while your pods are healthy.** Check Service `app` in `team-<team-name>`: port 80, and a selector that matches your ready pods.
- **kubectl says `Unauthorized`.** The token expired. Copy `config` again.
- **Radar Cloud is down.** Run Radar on your laptop with your kubeconfig (copy it early, so you have one):
  ```bash
  curl -fsSL https://get.radarhq.io | sh
  kubectl radar --kubeconfig ~/cnh-kubeconfig
  ```
  Its MCP server is `http://localhost:9280/mcp`. For Claude Code: `claude mcp add radar-local --transport http http://localhost:9280/mcp`. It sees only your cluster, so its tools take no `cluster`.

## Bringing a cluster of your own instead

You can run on a cluster you bring (kind, k3d, minikube or a cloud cluster, including one on your laptop) instead of the one we give you. It still counts for judging.

**Use your own Radar Cloud organization, not the hackathon's.** Don't add your cluster to Cloud Native Hackathon: every team could see it there. Your own organization is free: up to 3 clusters and 100 AI investigations a month. You also lose the public URL, Prometheus and organizer access to your cluster.

### On your laptop, in one command

This is for a few teams, if the shared cluster has a problem. You need Docker running (Docker Desktop, OrbStack, colima or Rancher Desktop) with about 4 GB of memory free, `kubectl`, `helm`, and k3d (`brew install k3d`; kind or minikube work too). In your copy of this repo:

```bash
./scripts/local-cluster.sh <team-name>
```

It creates a one-node cluster, `cnh-<team-name>`, set up like the one the hackathon gives you: Argo CD, Argo Rollouts, the Gateway API CRDs, and namespace `team-<team-name>` with its Argo CD project. Your repo's manifests work there as they are. The script stops twice, for things only you can do:

1. Connect the cluster to your own Radar Cloud organization: create it as in step 1 under [Any other cluster](#any-other-cluster), click **Connect a cluster**, name the cluster exactly `<team-name>`, and run the Helm command it shows in your terminal. The script never sees that command or its token.
2. Push your repo, if you haven't yet (it runs `./scripts/setup.sh` for you first).

Then it applies `argocd/application.yaml` and prints how to open the app (`kubectl port-forward`; there's no public URL on a laptop) and connect your agent to Radar's MCP server. Pick your own organization, not Cloud Native Hackathon, under "Organization access". Running it again is safe: it skips what's done. `./scripts/local-cluster.sh <team-name> --down` deletes the cluster.

AGENTS.md describes the hackathon cluster. Add this at the top of it, with your names filled in:

> **This team is NOT on the hackathon cluster.** We run our own cluster, `<team-name>`, on a laptop, in our own Radar Cloud organization `<your-org-name>`, not Cloud Native Hackathon; it's the only cluster there. There is no `https://<team-name>.cnh.radarhq.io`: check the new version through Radar's port-forward (or `kubectl -n team-<team-name> port-forward svc/app 8080:80`, then `http://localhost:8080`) instead. There's no Prometheus, and no budget or host limits apply here.

Venue wifi is slow. Before the event, run `./scripts/local-cluster.sh --pull` to download the images and charts (about 500 MB); `--images` lists them.

If your copy of this repo is older than the script, fetch it first:

```bash
curl -fsSL -o scripts/local-cluster.sh https://raw.githubusercontent.com/skyhook-io/cnh-starter/main/scripts/local-cluster.sh && chmod +x scripts/local-cluster.sh
```

### Any other cluster

It needs Argo CD, and Argo Rollouts if you use `examples/canary`.

1. **Create your organization.** Sign in at [app.radarhq.io](https://app.radarhq.io).
   - New to Radar Cloud: name your organization when it asks.
   - Already in Cloud Native Hackathon: open the menu under your name (bottom left) > **Create organization**. If the sign-in screen offers the hackathon invite, choose **Create a new organization instead**.

   Invite your teammates from **Settings > Members**. Check that the organization switcher shows your new organization, not Cloud Native Hackathon, before the next step.
2. **Connect your cluster.** Click **Connect a cluster** and name the cluster exactly `<team-name>`, the same name you give `setup.sh`. Your agent finds the cluster by that name. Run the Helm command it shows in a terminal whose kubectl context is your cluster (`kubectl config current-context`). The command carries your cluster's token: paste it into your terminal only, never into the repo, a chat or your agent.
3. **Point the repo at your team.** Make your copy and clone it (step 1 of [Ship your own code](#ship-your-own-code)), then:
   ```bash
   ./scripts/setup.sh <team-name>
   ```
   Delete the `- httproute.yaml` line from `deploy/kustomization.yaml`: it points at the hackathon's gateway, and your cluster has none.
4. **Tell your agent where it is.** AGENTS.md and the skills describe the hackathon cluster. Add this at the top of `AGENTS.md`, with your names filled in:
   > **This team is NOT on the hackathon cluster.** We run our own cluster, `<team-name>`, in our own Radar Cloud organization `<your-org-name>`, not Cloud Native Hackathon; it's the only cluster there. The Argo CD Application is `app-<team-name>` in namespace `argocd` (project `default`), not `app` in `team-<team-name>`; use that name and namespace wherever a skill says `app`. There is no `https://<team-name>.cnh.radarhq.io`: check the new version through Radar's port-forward (or `kubectl -n team-<team-name> port-forward svc/app 8080:80`, then `http://localhost:8080`) instead. No budget or host limits apply here.
5. **Push, and make the image public.**
   ```bash
   git commit -am "Set up team <team-name> on our own cluster" && git pull --rebase && git push
   ```
   Wait for the Action to go green (the repo's **Actions** tab), then make the image public once: on your repo's GitHub page, **Packages** > **Package settings** > **Change visibility** > **Public**. Your cluster can't pull a private image.
6. **Start GitOps.** Create the namespace, then apply `argocd/application-byo.yaml`, not `argocd/application.yaml`:
   ```bash
   kubectl create namespace team-<team-name>
   kubectl apply -f argocd/application-byo.yaml
   ```
   It uses Argo CD's `default` project.
7. **Connect your agent** as in [Connect your agent](#connect-your-agent), but pick **your own organization** under "Organization access", not Cloud Native Hackathon.
8. **Open your app** with Radar's port-forward, or `kubectl -n team-<team-name> port-forward svc/app 8080:80` and `http://localhost:8080`. It shows `version` and your latest commit.

## After today

Connect your own cluster to Radar Cloud. Hackathon participants get 3 months of Enterprise free: tell us your org name at the Radar table or in reply to our follow-up email, by Nov 30. Or run open-source Radar (`brew install kubectl-radar`), and star [skyhook-io/radar](https://github.com/skyhook-io/radar) if it helped.

## Help

Find Roy, Nadav or Arlen at the Radar table, or ask in `#cnh-hackathon` on the [Radar Discord](https://radarhq.io/community/chat).
