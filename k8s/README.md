# Kubernetes deployment

```
k8s/
  base/                 app: Deployment (+ migrate init container), Service, Ingress, HPA, PDB,
                        CronJob (purge expired offers), NetworkPolicy, ServiceAccount, config.env
  overlays/dev/         + in-cluster PostgreSQL & Redis, dev secrets, 1 replica, http://booking.localtest.me
  overlays/staging/     managed PostgreSQL/Redis via secret, 2-8 replicas, staging host
  overlays/production/  managed PostgreSQL/Redis via secret, 3-12 replicas, production host
```

Render any environment without applying: `kubectl kustomize k8s/overlays/staging`.

## Cluster prerequisites (staging/production)

- **ingress-nginx** (IngressClass `nginx`) and **cert-manager** with ClusterIssuers
  `letsencrypt` and `letsencrypt-staging`. Change the hostnames in the overlays from `*.example.com`.
- **metrics-server** for the HorizontalPodAutoscaler.
- A CNI that enforces NetworkPolicy (Calico, Cilium, most managed clusters).
- **Managed PostgreSQL 14+** (RDS, Cloud SQL, Azure Database) and **Redis 6+** (ElastiCache,
  Memorystore, Azure Cache), reachable from the cluster. Use TLS URLs (`?sslmode=require`, `rediss://`).
- If the GHCR package is private, add an imagePullSecret to the `booking-api` ServiceAccount.

## Secrets

The app reads the Secret **`booking-secrets`**. CD creates it from GitHub environment secrets on
every deploy (`.github/workflows/deploy.yml`). To create it by hand:

```bash
kubectl -n booking-prod create secret generic booking-secrets \
  --from-literal=DATABASE_URL='postgresql://booking:...@db.example.internal:5432/booking?sslmode=require' \
  --from-literal=REDIS_URL='rediss://:...@cache.example.internal:6379/0' \
  --from-literal=MFA_ENCRYPTION_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" \
  --from-literal=DUFFEL_ACCESS_TOKEN=''
```

`MFA_ENCRYPTION_KEY` must never change once users have enrolled; store it in your secrets
manager. For GitOps, replace this with External Secrets Operator or Sealed Secrets.

## Deploy CI service account

Give CD a kubeconfig for a service account that can only manage its own namespace, for example
a RoleBinding to the built-in `edit` ClusterRole in `booking-staging` / `booking-prod`
(plus `create`/`patch` on that Namespace object), then store it base64-encoded as the
`KUBE_CONFIG` environment secret.

## Local cluster (dev overlay)

Works with kind, k3d, minikube or Docker Desktop's Kubernetes:

```bash
docker build -t booking-assistant:dev .
kind load docker-image booking-assistant:dev        # or: minikube image load / k3d image import
kubectl apply -k k8s/overlays/dev
kubectl -n booking-dev rollout status deployment/booking-api
kubectl -n booking-dev port-forward svc/booking-api 8080:80   # http://localhost:8080
kubectl -n booking-dev exec deploy/booking-api -c api -- python auth_cli.py set-role you@example.com admin
```

## Operations

| Task | Command |
|---|---|
| Rollout status / history | `kubectl -n booking-prod rollout status deploy/booking-api` / `rollout history` |
| Roll back | `kubectl -n booking-prod rollout undo deploy/booking-api` |
| Migration status | `kubectl -n booking-prod exec deploy/booking-api -c api -- python migrate.py --status` |
| Admin tasks | `kubectl -n booking-prod exec -it deploy/booking-api -c api -- python auth_cli.py ...` |
| Health | `/healthz` (liveness: process up), `/readyz` (readiness: database reachable; Redis optional) |

Rolling updates use `maxUnavailable: 0`, a readiness gate on the database, and a 5 s preStop
delay, so deploys don't drop requests. Migrations must be backward compatible with the
previous version (see CONTRIBUTING.md, "Database changes").
