# Lightsail instance handoff

This deployment uses one Linux Lightsail instance running Docker Compose:

- ParadeDB/PostgreSQL stores the durable data in the `paradedb-data` volume.
- PostgreSQL is explicitly memory-tuned for a small single-instance host; the
  Compose defaults do not size its caches for a large developer workstation.
- `bootstrap` imports the committed CSV data and exits successfully.
- `app` starts only after bootstrap succeeds and exposes strict readiness at
  `/readyz`.
- Caddy is the only public-facing container. It currently serves plain HTTP
  for static-IP testing; it will manage HTTPS after a domain is configured.
- The deployment image includes the small `all-MiniLM-L6-v2` embedding model in
  its Hugging Face cache, so semantic retrieval works offline after startup.

## Before creating the instance

1. Run the local rehearsal in the README successfully.
2. Commit the deployment files, but never commit `.env`.
3. Generate a strong database password and keep it ready for the instance.
4. Choose the smallest instance that passes the local/rehearsal memory check.
   The semantic model and first vector rebuild consume more memory and CPU than
   a lexical-only startup. Validate bootstrap and warmed-query memory before
   treating the smallest plan as durable production capacity.

## Instance setup

1. Create an Ubuntu Lightsail instance in the region selected for Texas users.
   Lightsail does not currently offer a Texas region; select the closest
   available region after comparing prices and latency from Texas.
2. Attach a static IPv4 address.
3. Configure the Lightsail firewall for TCP 22 (restricted to your IP while
   possible), 80, and 443. Do not expose PostgreSQL port 5432.
4. Install Docker Engine and the Docker Compose plugin.
5. Clone the repository and create `.env` from `.env.example`. Set a unique
   `POSTGRES_PASSWORD`; set `HTTP_PORT=80` and `HTTPS_PORT=443` on the server.
6. Start the application with `docker compose up -d --build`, then check:

   ```bash
   curl -fsS http://127.0.0.1/healthz
   curl -fsS http://127.0.0.1/readyz
   docker compose exec app python scripts/benchmark_paradedb_retrieval.py
   ```

7. Configure automatic instance snapshots or a snapshot schedule. The database
   volume is the state that must be recoverable.

## Operational checks and backups

Run the smoke check after every deploy:

```bash
bash scripts/check_deployment.sh
```

After deploying the restriction-aware section sync for the first time, repair
all active restriction snapshots once:

```bash
docker compose exec -T app \
  python scripts/sync_tamu_public_sections.py --refresh-all-restrictions
```

The hourly systemd timer then refreshes restrictions for new or changed
sections during its normal run. The full option intentionally checks every
active CRN and is not appropriate for the hourly timer.

Create a logical database backup before upgrades or data refreshes:

```bash
bash scripts/backup_postgres.sh
```

Restore only after taking a fresh backup and confirming the replacement:

```bash
CONFIRM_RESTORE=yes bash scripts/restore_postgres.sh backups/course-search-<timestamp>.dump
```

## Automatic section refreshes

The sync command is one-shot. Install the included systemd service and timer
on Ubuntu to run it hourly without keeping a worker process alive:

```bash
sudo cp deploy/systemd/course-sections-sync.service /etc/systemd/system/
sudo cp deploy/systemd/course-sections-sync.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now course-sections-sync.timer
sudo systemctl start course-sections-sync.service
sudo journalctl -u course-sections-sync.service -n 100 --no-pager
```

The templates assume the repository is at
`/home/ubuntu/AggieCourses`. Edit `WorkingDirectory` if your
clone is elsewhere. Check the schedule with:

```bash
systemctl list-timers course-sections-sync.timer
```

The sync updates PostgreSQL and the section/filter projections; it does not
rebuild semantic embeddings.

## If Compose appears to freeze

Run commands with progress and inspect the blocking service separately:

```bash
docker compose ps
docker compose logs --tail=200 postgres bootstrap app caddy
docker stats --no-stream
docker compose build --progress=plain app
```

The first semantic-enabled image build and first startup vector rebuild can be
CPU- and memory-intensive. Do not delete the database volume while diagnosing;
never use `docker compose down -v` unless you intend to erase the catalog.

If the API is already running and only Caddy is stuck, restart just Caddy:

```bash
docker compose up -d caddy
```

If a previous Compose process is still running, inspect it from another SSH
session before stopping it. After a safe interruption, retry with:

```bash
docker compose --progress=plain build app
docker compose up -d
```

## Add a domain and HTTPS later

1. Point the domain's DNS A record to the instance static IP.
2. Replace `:80` in `Caddyfile` with the domain name, for example
   `courses.example.edu`.
3. Restart Caddy with `docker compose up -d caddy`.
4. Confirm Caddy obtains a certificate and `https://your-domain/readyz` works.

Caddy needs ports 80 and 443 publicly reachable for this certificate flow.

## Update and rollback

The repository publishes the deployment image to GitHub Container Registry from
the `main` branch. Set `APP_IMAGE` in the server's
`.env` file to:

```text
APP_IMAGE=ghcr.io/jasenio/aggiecourses:latest
```

If the package is private, authenticate the server once with a GitHub token
that has `read:packages`:

```bash
echo "$GHCR_TOKEN" | docker login ghcr.io -u jasenio --password-stdin
```

For an application update, back up PostgreSQL, pull the intended Git commit,
pull the prebuilt application image, and restart without building locally:

```bash
bash scripts/backup_postgres.sh
git pull --ff-only origin main
docker compose pull app bootstrap
docker compose up -d --no-build
bash scripts/check_deployment.sh
```

Keep a Git tag or image SHA for every deployed revision. To roll back, set
`APP_IMAGE` to the prior image tag, run `docker compose pull app bootstrap`,
and then run `docker compose up -d --no-build`. Before a database-affecting
update, take a Lightsail snapshot. A database rollback should use a verified
backup or snapshot rather than deleting the Compose volume.
