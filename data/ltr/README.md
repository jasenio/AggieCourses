# Course Recommender System

Course search for Texas A&M catalog data. The production path is FastAPI,
ParadeDB/PostgreSQL, and a static JavaScript frontend. PostgreSQL is the source
of truth; ParadeDB stores the rebuildable search projection.

## Run locally

The Compose stack starts ParadeDB, performs the one-time-safe bootstrap, starts
the API only after it is ready, and proxies it through Caddy. Create local
settings and start the stack:

```powershell
Copy-Item .env.example .env
docker compose up -d --build
docker compose ps
```

Open `http://127.0.0.1:8080`. The `.env` values are only development defaults;
replace `POSTGRES_PASSWORD` before any deployment.

Stop the stack without deleting its database with `docker compose down`.
To intentionally discard all local database data and bootstrap again, run
`docker compose down -v` followed by `docker compose up -d --build`.

## Operations

Health check:

```text
GET /health
GET /healthz
GET /readyz
```

`/healthz` confirms that the API process is responding. `/readyz` returns 503
until ParadeDB is reachable and its search projection is available. `/health`
provides diagnostic metadata and is not a deployment readiness signal.

Search examples:

```text
GET /search?q=accounting&limit=20
GET /search?q=calculus&prefix=MATH&limit=20
GET /search?rank=most_enrolled&limit=20
GET /search?rank=gpa&attribute=Distance%20Education&limit=20
```

Refresh the active term without rebuilding the catalog projection:

```powershell
crs\Scripts\python.exe scripts\sync_tamu_public_sections.py
```

Use `--dry-run` to inspect changes. Pass `--term 202631` to select a specific
active PostgreSQL term.

## Benchmark and checks

Run the fast contract checks from the workspace:

```powershell
crs\Scripts\python.exe -m unittest discover -s tests -v
```

Measure the running container's FastAPI search path with the fixed smoke suite:

```powershell
docker compose exec app python scripts/benchmark_paradedb_retrieval.py
```

The benchmark writes an ignored JSON report under `artifacts/`.

Run the lightweight deployment smoke check:

```bash
bash scripts/check_deployment.sh
```

Create and restore PostgreSQL backups from the host:

```bash
bash scripts/backup_postgres.sh
CONFIRM_RESTORE=yes bash scripts/restore_postgres.sh backups/course-search-<timestamp>.dump
```

Treat restore as a destructive operation against the current database. Take a
new backup first and stop the API if you are restoring over a live instance.

The deployment image includes the small `all-MiniLM-L6-v2` embedding model in
its Hugging Face cache, so semantic retrieval works offline after startup. The
API fails over to lexical retrieval only if a deliberately rebuilt image is
missing that model.

## Data

The committed bootstrap data consists of the catalog, attributed section
snapshots, full registration restrictions, major taxonomy, subject context, and
grade history. The application reads from PostgreSQL after bootstrap.

Legacy automated annotation, AEFIS, and search-migration workflows remain
outside the production runtime. The local human LTR workflow is documented
below. See
[docs/runtime-and-offline-boundaries.md](docs/runtime-and-offline-boundaries.md)
for the current boundary.

## Human LTR annotation

The local workspace reads all 250 cases from `data/human_test_queries.csv`.
For each query it builds a deterministic 25-course pool: 10 from RRF reranked
by the cross-encoder, 5 BM25, 5 semantic, and 5 deterministic random courses.
Duplicate slots are filled from the remaining RRF/cross-encoder ranking.

Start the normal API and open `http://127.0.0.1:8000/annotation`. Judgments are
checkpointed in browser IndexedDB. Export produces training JSON containing
each 0–3 label, sampling provenance, and the versioned 15-feature vector. This
workflow does not read or depend on former course-relevance annotations.

The cross-encoder defaults to `cross-encoder/ms-marco-MiniLM-L6-v2`. Cached
Hugging Face models are used by default; set `HF_HUB_OFFLINE=0` before startup
only when deliberately downloading it. The workspace visibly records when
cross-encoder scoring was unavailable and RRF fallback was used.

After exporting all judgments:

```powershell
crs\Scripts\python.exe -m pip install -r requirements.txt
crs\Scripts\python.exe scripts\ltr\train_ltr_model.py --annotations artifacts\human-ltr-annotations.json
```

## Lightsail

This repository is prepared for a single Lightsail instance running Docker
Compose. The instance setup, static IP, firewall, backups, domain/TLS switch,
and first deployment steps are in
[docs/lightsail-instance-handoff.md](docs/lightsail-instance-handoff.md).
