#!/usr/bin/env bash
set -euo pipefail

output_path="${1:-backups/course-search-$(date -u +%Y%m%dT%H%M%SZ).dump}"
mkdir -p "$(dirname -- "$output_path")"

docker compose exec -T postgres sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
  > "$output_path"

echo "Wrote PostgreSQL backup to $output_path"
