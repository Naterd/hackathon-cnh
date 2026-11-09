# Postgres with CloudNativePG

Optional. Use it if your app needs a database. The CloudNativePG operator is already in your cluster, so the database is one `Cluster`.

1. Copy `cluster.yaml` into `deploy/` and add it to `resources:` in `deploy/kustomization.yaml`.
2. Give your app the connection string. In `deploy/deployment.yaml`, add to the container:

   ```yaml
   env:
     - name: DATABASE_URL
       valueFrom:
         secretKeyRef:
           name: db-app
           key: uri
   ```

3. Push. Argo CD creates the database, and it's ready in about a minute. Until the operator has made Secret `db-app`, the app's new pods wait with `CreateContainerConfigError`, then start on their own. Have the app retry its first connection.
4. See it in Radar: Cluster `db` under Resources (kind `Cluster`, group `postgresql.cnpg.io`), its pod `db-1`, its PVC and the `db-rw` Service. Your agent can wait for the Cluster's `Ready` condition with `wait_for`.

How the app reaches it: Service `db-rw`, port 5432, database `app`, user `app`. Secret `db-app` (made by the operator, not in git) has `username`, `password`, `host`, `port`, `dbname` and the full `uri`. The data lives on the PVC, so it survives pod restarts; it's deleted with your cluster on Nov 13.

Budget: the database is one pod (100m CPU and 256Mi of requests, 500m and 512Mi of limits) and one 1Gi PVC, out of your team's 2 CPU / 4 GiB of requests and 4 PVCs / 20 GiB.
