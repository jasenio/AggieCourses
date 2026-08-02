#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 || ! -f "$1" ]]; then
  echo "Usage: $0 path/to/backup.dump" >&2
  exit 2
fi
if [[ "${CONFIRM_RESTORE:-}" != "yes" ]]; then
  echo "This replaces database objects. Re-run with CONFIRM_RESTORE=yes." >&2
  exit 2
fi

docker compose exec -T postgres sh -c \
  'pg_restore --clean --if-exists --no-owner -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  < "$1"

echo "Restored PostgreSQL backup from $1"
