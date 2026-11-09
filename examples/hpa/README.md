# Autoscaling with an HPA

Optional. Turn it on if your app gets real load worth showing; skip it otherwise.

1. Copy `hpa.yaml` into `deploy/` and add it to `resources:` in `deploy/kustomization.yaml`.
2. Delete the `replicas:` line from `deploy/deployment.yaml`. Otherwise Argo CD puts the replica count back on every sync and fights the autoscaler.
3. Push. Argo CD applies it.
4. Send real load at your URL (an event-day rush, a load test, everyone in the room opening it) and watch the HPA scale out in Radar: the HPA's page, and the scale-up on the timeline.

The HPA scales on CPU against your container's `requests.cpu`, so keep a CPU request in the Deployment. Your team's budget caps how far it can scale: your workloads get 4 CPU of limits in total, and each starter pod has a 500m CPU limit, so 8 pods at most. `maxReplicas: 4` uses half of that and leaves room for the extra pod a rollout starts and for anything else you run. Raise it only if you lower the pod's CPU limit.
