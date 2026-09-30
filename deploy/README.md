# Deploying Adhigrahan Radar

**What this directory is.** `deploy/` is this application's deployment configuration for one
specific Kubernetes cluster — a single-node k3s instance owned by a maintainer, which already hosts
several unrelated applications under the `*.upayan.dev` wildcard. It builds two container images
and describes the workloads that run them. **It changes nothing about how the application behaves.**
No file in `backend/`, `pipeline/`, `frontend/` or either test suite reads anything here, nothing
here is imported at runtime, and `make build`, `make api`, `make web` and `make test` are exactly
what they were before this directory existed. If the project would rather not carry a cluster's
configuration, deleting `deploy/`, the two `Dockerfile.*` files and
`.github/workflows/images.yml` leaves the application untouched and fully working — that is also
why no runtime dependency, no configuration flag and no code path was added for it.

It is also not an invention: `docs/plans/2026-09-09-adhigrahan-radar-implementation-blueprint.md`
§M.2 ("Add containerisation", lines 1049–1052) sketches `Dockerfile.api` (install requirements, run
the build at image-build time so the container ships with a populated `vivaad.db`, `CMD uvicorn`),
`Dockerfile.web` (a node:20 build stage serving static output) and a compose file for a
demo machine. This directory is the first two, unchanged in substance, plus the Kubernetes
manifests the cluster needs. The compose file is deliberately absent: `docker compose up` is a
demo-laptop path, and the deployment here is a cluster. One deliberate deviation from the sketch:
it proposes `python:3.12-slim` where this uses `python:3.11-slim`, because `ci.yml` pins 3.11 and
that is the only interpreter the project's lint, tests and pipeline are actually validated on
(`pyproject.toml` allows 3.11–3.13, so both are legitimate; see the comment in `Dockerfile.api`).

Worth naming plainly, since `CONTRIBUTING.md`'s "What not to build" lists *cloud orchestration*:
that rule exists so the project never grows a cloud dependency it does not need, and this directory
cannot become one. It adds no runtime code path, no service, no credential and no test to the
application; `make doctor`, `make setup`, `make build`, `make test` and the CI gates behave
identically with it deleted. Everything below is the deployment *of this repository*, not a
dependency *in* it.

## The two images

| Image | What it is |
|---|---|
| `ghcr.io/ujjwal160107/adhigrahan-radar/api:edge` | `Dockerfile.api`: python 3.11 + `requirements.txt`, the repo copied in, and **the offline pipeline run at image-build time** (`python pipeline/run_all.py --skip-handoff`), so the SQLite database, the joblib models and the tier-2 fallback JSON are baked in. Runs uvicorn on `0.0.0.0:8000` as UID/GID 10001 (non-root). |
| `ghcr.io/ujjwal160107/adhigrahan-radar/web:edge` | `Dockerfile.web`: node:20 build stage (`npm ci`, then `npm run build` = `tsc && vite build`) → `nginx:alpine` serving `frontend/dist`. |

Both are published by `.github/workflows/images.yml` on pushes to `main` and by manual dispatch,
using the workflow's own `GITHUB_TOKEN` with `permissions: packages: write`. No credential, secret
or PAT is stored in this repository; the owner in the image name is lowercased in the workflow
because GHCR rejects uppercase paths. `:edge` is the moving tag the manifests reference, and a
short-lived `:sha-<commit>` tag goes up beside it so a rollback does not have to guess which build
`edge` currently points at. `ci.yml` is untouched — it remains the lint/test gate, and it runs the
same two build commands.

The workflow derives the GHCR namespace from the repository it runs in, so on `main` here it
publishes `ghcr.io/ujjwal160107/adhigrahan-radar/{api,web}` — the paths `deploy/k8s/kustomization.yaml`
names. Run from a fork instead and the images land under *that* fork's owner, which the manifests do
not point at: either merge to `main` first, or change the two `images[].name` entries in
`deploy/k8s/kustomization.yaml` (and the two `image:` fields) to match.

Because images are built from `main` only, the workflow does not run on this pull request. To cut a
release: merge, wait for the `images` workflow, then let the cluster sync.

## Hostnames

| Host | Backend |
|---|---|
| `radar.upayan.dev` | `adhigrahan-radar-web` Service, port 3000 (the SPA) |
| `api-radar.upayan.dev` | `adhigrahan-radar-api` Service, port 8000 (FastAPI/uvicorn) |

Both are on one Ingress (`deploy/k8s/ingress.yaml`), `ingressClassName: traefik`, **no `tls:`
block**: the cluster serves the `*.upayan.dev` wildcard through Traefik's default `TLSStore` (a
Cloudflare Origin CA certificate), so a per-Ingress certificate would be wrong as well as
unnecessary. There is no second hostname to add, no path prefix, and no reverse proxy inside the
web container — the SPA calls the API directly at the baked origin above.

## Env vars the deployment sets

`deploy/k8s/deployment-api.yaml` sets exactly three, all read by code in this repository:

| Name | Read by | Value |
|---|---|---|
| `VIVAAD_DB` | `backend/db.py:10` | `/app/data/output/vivaad.db` |
| `VIVAAD_FALLBACK_DIR` | `backend/fallback.py:15` | `/app/data/output/fallback` |
| `CORS_ORIGINS` | `backend/main.py:71` | `https://radar.upayan.dev` |

`.env.example` documents the first two and the third; the absolute values above are the same paths
`Dockerfile.api` bakes in as image defaults, repeated in the manifest because the Deployment is what
mounts the volume.

One more name matters but is **not** a runtime variable: `VITE_API_URL`
(`frontend/src/api/client.ts:33`) is inlined into the bundle by Vite at **build time**, defaulting
to `http://localhost:8000` and set to `https://api-radar.upayan.dev` in `Dockerfile.web` and in
the image workflow. Changing it requires rebuilding the web image; setting it on the running
container does nothing.

`deploy/k8s/deployment-web.yaml` sets no environment variables at all.

### CORS: required, and satisfied without touching code

The API allows cross-origin requests only from the origins in `CORS_ORIGINS`
(`backend/main.py:69–76`), whose default is the two Vite dev origins
(`http://localhost:5173,http://127.0.0.1:5173`). The SPA is served from `radar.upayan.dev` and
calls `https://api-radar.upayan.dev`, which is a different origin (scheme and host both differ),
so **`https://radar.upayan.dev` must be listed or every request from the deployed frontend
fails preflight**. It is listed, as the third row of the table above. Nothing else is needed:
`allow_methods` already covers `GET` and `POST` (the two the frontend uses) and `allow_headers` is
`["*"]`, which covers the demo-grade `X-Role` header (`backend/auth.py`) the app reads. No
application code was changed for this.

Note what the API is, on a public hostname: `X-Role` is a demo-grade header, not authentication, and
the write endpoints are open. That is the application's own documented posture, and this deployment
does not add or remove a gate — it only makes the surface reachable.

## The data volume: how the SQLite store is seeded, and why

The API is not read-only. `backend/main.py:80`'s audit middleware writes an `AuditLog` row for
**every** request, and `POST /watchlist` and `POST /projects/{id}/interventions` write user data.
`data/output/vivaad.db` is therefore both a build artifact and a live store, which is why it is a
`PersistentVolumeClaim` (`adhigrahan-radar-data-pvc`, 1Gi, RWO) rather than part of the container
filesystem — a container filesystem would discard the audit trail and the watchlist on every
rollout.

The two halves of that:

1. **Seed.** `deployment-api.yaml` has one initContainer (`seed-data`) which, **only when
   `/data/vivaad.db` does not exist**, copies the image's baked `/app/data/output` into the empty
   volume (`cp -a`), and otherwise exits without touching anything. So a fresh volume starts from
   the exact database, models and fallback cache the image was built with, and a volume that has
   been written to since is never overwritten — not on a rollout, not on a restart. If a seed is
   ever interrupted, the database is regenerable (the pipeline is committed): delete the volume's
   contents and let the next pod seed it again.
2. **Mount.** The volume is mounted over `/app/data/output` in the API container, which is why the
   absolute `VIVAAD_DB` and `VIVAAD_FALLBACK_DIR` paths matter — a relative default would follow the
   process's working directory instead of the mounted volume.

## What lives where

Tenant-owned, i.e. **in this repository** (`deploy/k8s/`, rendered by `kustomize build deploy/k8s`):
the `adhigrahan-radar-api` and `adhigrahan-radar-web` Deployments and Services, `adhigrahan-radar-ingress`, three
NetworkPolicies (`adhigrahan-radar-default-deny-ingress` plus one allow for each workload, allowing Traefik
from `kube-system` and same-namespace callers), and `adhigrahan-radar-data-pvc`. There is no egress policy
because the deployment needs none: the pipeline that builds the database runs at image-build time,
never at runtime.

Cluster-owned, i.e. **not here**, and managed in the cluster's own repository:

- **The Namespace** (`adhigrahan-radar`) — created and labelled there, including its Pod Security Admission
  labels. Nothing in `deploy/k8s/` declares it; the kustomization only sets `namespace: adhigrahan-radar`
  so every object lands in it.
- **The ArgoCD Application** — this directory is the `source.path` of an Application that also
  carries the `argocd-image-updater` annotations that track `:edge` by digest.
- **The static PersistentVolume** the PVC binds to — see below.

Object *names* (`adhigrahan-radar-api`, `adhigrahan-radar-web`, `adhigrahan-radar-ingress`, the NetworkPolicy names) are
the tenant's existing names and are deliberately unchanged, together with the `app: adhigrahan-radar-api` /
`app: adhigrahan-radar-web` selector labels, the Service ports (8000/3000) and the hostnames. A Kubernetes
Deployment's selector is immutable, and keeping the rest means the cutover is a change of manifest
source, not a change of contract.

### The static PV the cluster side creates

`adhigrahan-radar-data-pvc` uses `storageClassName: ""` and `volumeName: adhigrahan-radar-data-pvc-vps`, so it
binds **statically** and never to a dynamically provisioned `local-path` volume — a claim that could
silently reappear on the node's root disk. The cluster side creates the matching PV (the pattern its
other tenants use: a `vps-data`-backed static PV with node affinity, e.g.
`meghmitra-postgres-pvc-vps`). Required:

```yaml
apiVersion: v1
kind: PersistentVolume
metadata:
  name: adhigrahan-radar-data-pvc-vps          # must equal the PVC's volumeName
  annotations:
    # The cluster's invariant for PVs that hold data: a GitOps mistake must not be able to prune it.
    argocd.argoproj.io/sync-options: Delete=false,Prune=false
spec:
  capacity:
    storage: 1Gi                        # >= the PVC's request
  accessModes: [ReadWriteOnce]
  volumeMode: Filesystem
  persistentVolumeReclaimPolicy: Retain # the store outlives the claim
  storageClassName: ""                  # matches the PVC; "" means static binding
  hostPath:
    path: /srv/data/adhigrahan-radar-data      # the 20 GB vps-data volume (docs/storage.md)
    type: Directory
  nodeAffinity:
    required:
      nodeSelectorTerms:
        - matchExpressions:
            - key: kubernetes.io/hostname
              operator: In
              values: ["vps"]
```

Two things that are easy to get wrong:

- **`type: Directory` requires the directory to exist**, and the API (UID/GID 10001) must be able to
  write in it. The kubelet does not change ownership on a `hostPath` volume, so on the node:
  `install -d -o 10001 -g 10001 -m 0750 /srv/data/adhigrahan-radar-data`. That is also the UID `Dockerfile.api`
  runs as — the app writes to its store on every request, so an unwritable volume is a 500, not a
  log line.
- Until the PV exists, the claim stays `Pending` and the API pod stays `Pending` with it. That is
  intended: the alternative is a pod that boots against a volume nobody meant to give it.

## Cutover: the current occupant of these hostnames

`radar.upayan.dev` and `api-radar.upayan.dev` are **already serving a different
application** — a Node web/API pair over PostGIS, with images from another registry and class-A data
in its own PVC/PV. The hostnames and the namespace (`adhigrahan-radar`) are shared deliberately: this
deployment replaces that one, and the ArgoCD Application for the tenant will point at this
directory.

**That existing deployment must not be removed until this one is validated.** The replacement is a
different application, not a version of the old one: different runtime, different store (SQLite, a
pipeline artifact, versus PostGIS), different schema, no data migration path between them. So the
sequence is: publish these images → create the PV above → point the Application's `source` here →
validate the new pods serve both hosts → *only then* retire the old workloads, its PVC/PV and its
secrets. Its store has class-A data (its own backup registry entry) and must be left bound or
released, but untouched, until that decision is made explicitly.

Until the Application is switched, nothing in this repository is running anywhere.

## Local use

```bash
# API: pipeline runs inside the build; run it and talk to it exactly as a reviewer would
docker build -f Dockerfile.api -t ar-api:local .
docker run --rm -p 8000:8000 ar-api:local
curl -sS localhost:8000/auth/session
curl -sS 'localhost:8000/projects?limit=2'

# Web: the API origin is baked in at build time, so point it at your local API
docker build -f Dockerfile.web --build-arg VITE_API_URL=http://localhost:8000 -t ar-web:local .
docker run --rm -p 3000:3000 ar-web:local
curl -sS localhost:3000/ | head -20

# The manifests, without a cluster
kustomize build deploy/k8s
kubectl apply --dry-run=server -k deploy/k8s
```

### A note on the health probes

This application has no health route — there is no `/health` anywhere in `backend/`, and inventing
one would be an application change. `deployment-api.yaml`'s probes therefore use
`GET /auth/session` (`backend/routers/auth.py:8`), the cheapest real endpoint the API has: it is
served by the app's own router, reads no database, and cannot be answered by the tier-2 fallback
cache (`backend/fallback.py` only substitutes responses for `>=500`s). Two consequences are
deliberate: every probe request is audited like any other request, so the probe periods are 10s
(readiness) and 30s (liveness) rather than the 5s/15s used elsewhere on this cluster — 5s/15s would
write roughly 23,000 `AuditLog` rows a day into a single-writer SQLite file; and a future `/health`
route would let those periods tighten. If you add one, change both paths here and this note
together.
