# Dependencies, configuration and one-off work

Use these patterns when your project needs them. They are not judging requirements.
This team has its own virtual cluster (vcluster), with cluster-admin access inside it.
Replace `team-__TEAM__` with your app namespace. On Radar Cloud, call `list_clusters`,
select the entry whose `name` exactly matches your team name, and pass its `id` as
`cluster` on every subsequent tool call below. Never select `cnh` or another team.
Use your app namespace unless deliberately working in another namespace in your cluster.
Discover the tools your connected Radar advertises before using a tool: a tool
described in newer documentation may not be deployed yet. Local OSS Radar has no
`cluster` parameter; follow each advertised schema.

## Keep credentials out of your public repository

First create `app-credentials` in your namespace through Radar's resource editor, using
`type: Opaque` and `stringData.api-key` for the value. This Secret is deliberately
outside Argo CD: do not add its manifest to `deploy/`, a PR, your image, or a build log.
The generic `apply_resource` MCP tool can create a Secret too, but supplying its value
to an agent puts it in that agent's conversation/tool history. Prefer entering it in
the UI. A Secret reference in git does not create the Secret.

Once the Secret exists, commit and deploy a `secretKeyRef` in your Deployment, never the credential value:

```yaml
env:
  - name: API_KEY
    valueFrom:
      secretKeyRef:
        name: app-credentials
        key: api-key
```

After changing a Secret consumed through environment variables, restart the workload
through `manage_workload`, then use `wait_for` to check the rollout. Don't print the
credential to prove it exists; check that the app's authenticated operation succeeds.

## Run one-off work as a Job

For a migration, import or batch operation, create a uniquely named Job through
`apply_resource` with `mode=create`. Use your already-built public application image,
its actual command, and only the permissions that command needs. This example assumes
your image contains `node` and `scripts/import.js`; change both for your app.

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: app-import-001
  namespace: team-__TEAM__
spec:
  backoffLimit: 0
  activeDeadlineSeconds: 300
  ttlSecondsAfterFinished: 3600
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: import
          image: ghcr.io/YOUR-OWNER/YOUR-REPO:YOUR-BUILT-TAG
          command: [node, scripts/import.js]
          securityContext:
            runAsNonRoot: true
            allowPrivilegeEscalation: false
            seccompProfile: {type: RuntimeDefault}
            capabilities: {drop: [ALL]}
          resources:
            requests: {cpu: 100m, memory: 128Mi}
            limits: {cpu: 500m, memory: 256Mi}
```

Use `wait_for` with `kind=job`, the namespace/name and `condition=complete`. A `waiting`
result means the Job has not finished; call again within your own deadline. A `failed`
result needs investigation. Read `get_workload_logs` or inspect the Job's Pods to see
what the command actually did. Kubernetes completion only establishes that the process
exited successfully; verify the application's intended result too.

Jobs and non-git Secrets are exceptions to the starter's normal GitOps rule: they are
not resources Argo CD owns. Do not reuse a Job name to rerun work, or put a completed
one-off Job into the continuously reconciled app manifests. Make retries safe for your
data before rerunning a migration or import.

## Add a dependency

For Postgres, use the [Postgres example](../postgres/README.md): the CloudNativePG
operator is already installed, so create a database `Cluster`, not another operator.

For another small database or queue, put its namespace-scoped Deployment/StatefulSet and
Service in `deploy/`, add them to `deploy/kustomization.yaml`, and push. Use a PVC when
data must survive a Pod replacement. Check the cluster's available storage and your
quota before choosing requests; your dependency shares the team's quota with the app.

Your team's AppProject allows any source repository, namespaces in your virtual
cluster, and cluster-scoped resources. A chart can be managed by an Argo CD
Application in namespace `team-__TEAM__`, project `team-__TEAM__`. Use an exact chart
version and check its rendered resources first. Operators, CRDs, cluster-wide
Roles/Bindings and additional namespaces are allowed inside your team's cluster.
They still share its total budget and cannot bypass the host's restrictions on
privileged, hostPath, hostNetwork or hostPID Pods. Leave the pre-installed Argo CD,
Rollouts, Prometheus, CloudNativePG operator, metrics components and AppProject unchanged.

If an Application needs `CreateNamespace=true` or `ServerSideApply=true`, set its
`syncOptions`. Before syncing, read the live Application and pass its complete current
list as `sync_options`; do not reuse a cached list or pass only the new option.
This also works with Radar v1.19.0, which does not inherit the list automatically.
Radar’s current sync implementation inherits it when omitted; an explicit list replaces it for that sync.
Do not run a direct Helm upgrade against resources already managed by Argo CD or Flux.

A native Helm release can be managed through the Helm CLI (with the person’s
kubeconfig) or Radar’s `manage_helm` tool when advertised. For `manage_helm`, the owner must preconfigure
the chart repository used by that Radar instance first; cluster-admin access does not
configure Radar's sources. For `manage_helm`, upgrade values merge with stored values by default; full
replacement requires `replace_values: true`. Keep the starter app and dependencies
already owned by Argo CD in GitOps. For a separate dependency, choose one owner: a
GitOps Application, a native Helm release, or direct manifests through `apply_resource`
that neither controller owns. Do not mix these paths for the same resources. Native
Helm is not required for this track.

## Inspect the running application

- For a public endpoint, use your agent's HTTP/browser tools against your team's URL.
- For Kubernetes state and failure evidence, use Radar's resource/log tools or `diagnose`.
- If advertised and enabled, `curl_service` GETs a selector-backed internal Service
  from in-cluster Radar and returns its HTTP status/body. It sends no credentials and
  is not a test of the public ingress path. Use `namespace`, Service `name` and its
  numeric `port`, plus the endpoint `path`.
- For an in-container check, use `exec_pod` if the operator has enabled it. It accepts an
  argv array, needs the executable in the image, and runs with your Kubernetes identity.
  It has no interactive input/TTY. Never put literal secrets in argv: audit logs can
  retain the command. If it is absent, ask staff; installing a client-side MCP connection
  does not enable the server's exec capability.
- A failed command or HTTP response is evidence, not automatically a Kubernetes fault.
  Check the application's response/body and expected behavior too.

`diagnose` gathers Kubernetes evidence; it does not start a hosted investigator.
For optional hosted analysis, follow [the investigation workflow](#delegate-a-real-question-to-hosted-ai-sre)
below, which covers MCP discovery, UI fallback, allowance and saved answers.

### Tool prerequisites and outcomes

`wait_for` may report `blocked` (for example, scheduling or quota prevents progress).
Read its reason and relevant events before retrying. `curl_service` requires
in-cluster Radar, a selector-backed Service with a ClusterIP (not headless), HTTP
tools enabled by the operator (`--mcp-http-tools` or Helm `mcp.httpTools: true`), and `get services/proxy` on
the target Service. Without auth, that grant must belong to Radar's ServiceAccount.
The default Radar chart does not grant it.

For Radar’s `manage_helm` tool, source configuration is a prerequisite outside MCP. Local Radar
uses its Helm repository config (for example a named source added with `helm repo add`).
Operator-managed OSS can use startup Helm configuration, including `helm.ociSources`.
That chart setting is rejected in Cloud mode. Cloud has no self-service first-install
source setup in this workflow; raw owner configuration is pod-local and lost on Pod
replacement. If Radar has no maintained source, use a GitOps Application or the
Helm CLI with its separately configured repository and the person’s kubeconfig;
cluster-admin access alone does not make `manage_helm` ready. Upgrade overrides merge maps, but
lists replace as whole units and null/type changes remove prior values. Replacing
a container with hidden credentials is refused unless you explicitly replace the
whole reviewed values configuration.

## Confirm that your push is actually live

The image tag is the first seven characters of the app commit that triggered
`build-and-deploy`, not the later bot commit that updates the manifests.

1. Save the pushed commit SHA. Check that commit's GitHub Actions run, for example
   `gh run list --workflow build-and-deploy --commit <FULL-SHA>`, then
   `gh run watch <RUN-ID> --exit-status`. If the build failed, inspect its logs before
   debugging Kubernetes. Retry briefly if the run is not registered yet, within the
   five-minute deadline; a missing/cancelled/superseded run does not prove a build.
   Markdown-only changes need no deploy. A kustomization-only push also skips the
   Action: retain the existing image tag and verify the intended configuration
   after sync instead of expecting a new app version. Other manifest changes build.
2. Read the resulting `deploy/kustomization.yaml` from the remote repository and
   confirm `newName`/`newTag` identify that build. A successful build for another
   commit is not the evidence you need.
3. Read the Argo Application with `get_resource`. Refresh/sync through `manage_gitops`
   if necessary, passing the complete freshly read Application `syncOptions` as
   `sync_options`. Read the live Deployment (or Rollout if you changed the example)
   and confirm its pod-template image matches the expected tag. If not, keep waiting
   on the desired revision; don't report the existing healthy app as the new build.
4. Use `wait_for` for rollout completion, then request your public `/api/sessions`.
   The starter's `version` field must equal that seven-character tag (or the unchanged
   image tag when the Action intentionally skipped a configuration-only push). Keep an equivalent
   version endpoint if you replace the starter app. Verify the feature you changed too.

## Delegate a real question to hosted AI SRE

These tools live on the organization `/mcp` endpoint, not `/c/<cluster-id>/mcp`.
Discover `start_investigation` first; if absent, use the Cloud UI. Read/list/stop
may still be available when new starts are disabled. `diagnose` collects evidence itself,
while `start_investigation` starts a hosted AI investigator and can consume allowance.
It starts with a resource assessment; arbitrary questions are follow-up turns.
The investigator gathers evidence and suggests steps; these tools do not apply remediation.
Start only when the person has requested or approved a hosted investigation.
Hosted investigations cost the team nothing at the hackathon: they come from the
organization's allowance. An existing request to investigate
is sufficient—do not ask again for the same authorized work.

- Get `cluster` from the `list_clusters` entry whose `name` exactly matches your team, then use `get_resource` to verify the exact target. Missing or mistyped targets can still consume allowance.
- If a start response was lost, you lost the run ID, or the person started it in the UI, use `list_investigations` for that cluster before starting again. It returns your own runs in `result.runs`; select the intended target/run, use its `id` as `run_id`, and continue with `get_investigation` instead of starting again. A completed run started again can consume allowance again. If listing is unavailable, recover the existing run in Cloud instead of assuming another start is needed.
- Call `start_investigation` with, for example,
  `{"cluster":"clu_example","group":"apps","kind":"Deployment","namespace":"team-__TEAM__","name":"app"}`.
  When coming from a Radar issue, add its ID as `issue_id`; the Hub validates its target
  and can associate the same counted issue episode. Do not invent an issue ID.
- Save `structuredContent.result.id` as `run_id`. Call `get_investigation` with
  `cluster` and `run_id`; keep `next_after`, and use it as `after` on the next event read.
  Drain `has_more` pages. `has_more:false` only means currently saved events are drained.
- Poll with a bounded interval/deadline until `done`, `error`, or `stopped`; `running` and `stopping` are nonterminal. The initial assessment is in `findings` when available. For status-only checks use `include_events:false`
  and omit `after`; never start a new investigation just to check progress.
- Only ask `follow_up_investigation` when it is advertised and `canContinue:true`. Supply `question` and read
  events after your saved cursor for the answer, usually in `done.diagnosis.report`.
  Saved `findings` can still be the prior assessment; don't present them as the new answer.
- Stop through `stop_investigation` when appropriate, then poll until `done`, `error`,
  or `stopped`. `stopping` is not terminal. Check tool errors before reading result fields.

New interactive runs start private, whether started in the UI or through MCP.
Sharing a run with the organization exposes it to all teams, not just teammates.
Automatic analyses follow a separate visibility policy; MCP reads only your own runs.
MCP cannot change sharing or accept disclosure.

If disclosure or allowance blocks a start, surface the refusal and suggest asking at the
Radar table. A human accepts organization disclosure in the UI; agents cannot do that
or raise the limit.
All teams share the configured organization allowance and concurrency pool.

Hosted investigation starts and follow-ups require the Hub operator to enable
`HUB_MCP_INVESTIGATION_STARTS_ENABLED`. Read/list/stop may be advertised while
starts are disabled. That switch uses the existing shared organization limits;
it does not allocate a separate team budget. Use Radar Cloud's UI if starting
through MCP is unavailable.
