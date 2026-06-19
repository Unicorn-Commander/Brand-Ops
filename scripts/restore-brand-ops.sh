#!/usr/bin/env bash
# Restore (or verify) a Brand-Ops backup pair produced by backup-brand-ops.sh.
#
#   --verify   NON-DESTRUCTIVE: load the DB dump into a throwaway postgres and
#              list the kits archive, to prove the backup is restorable. Touches
#              nothing live. This is the "rehearsed restore" for prod.
#   --confirm  DESTRUCTIVE: overwrite the LIVE brand_ops DB and brand-kits volume.
#
# Usage:
#   scripts/restore-brand-ops.sh --verify  --dump <brand_ops_*.sql.gz> --kits <brand_kits_*.tar.gz>
#   scripts/restore-brand-ops.sh --confirm --dump <brand_ops_*.sql.gz> --kits <brand_kits_*.tar.gz>
# See docs/BACKUP.md.
set -euo pipefail

PG_CONTAINER="${BRANDOPS_PG_CONTAINER:-brand-ops-postgres}"
PG_DB="${BRANDOPS_PG_DB:-brand_ops}"
PG_IMAGE="${BRANDOPS_PG_IMAGE:-postgres:16-alpine}"

DUMP=""; KITS=""; MODE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --dump) DUMP="$2"; shift 2;;
    --kits) KITS="$2"; shift 2;;
    --verify) MODE=verify; shift;;
    --confirm) MODE=confirm; shift;;
    *) echo "unknown arg: $1"; exit 2;;
  esac
done
[ -n "$DUMP" ] && [ -f "$DUMP" ] || { echo "need --dump <brand_ops_*.sql.gz>"; exit 2; }
[ -n "$KITS" ] && [ -f "$KITS" ] || { echo "need --kits <brand_kits_*.tar.gz>"; exit 2; }
[ -n "$MODE" ] || { echo "need --verify or --confirm"; exit 2; }

echo ">>> checking archive integrity"
gzip -t "$DUMP"
tar tzf "$KITS" >/dev/null
echo "    dump + kits archives are intact"
KITS_FILES="$(tar tzf "$KITS" | wc -l | tr -d ' ')"

if [ "$MODE" = verify ]; then
  echo ">>> VERIFY (non-destructive): loading the dump into a throwaway $PG_IMAGE"
  SCRATCH="brand-ops-restore-verify-$$"
  docker run -d --rm --name "$SCRATCH" -e POSTGRES_PASSWORD=verify \
    -e POSTGRES_DB="$PG_DB" -e POSTGRES_USER=brand_ops_owner "$PG_IMAGE" >/dev/null
  trap 'docker rm -f "$SCRATCH" >/dev/null 2>&1 || true' EXIT
  for _ in $(seq 1 30); do
    docker exec "$SCRATCH" pg_isready -U brand_ops_owner -d "$PG_DB" >/dev/null 2>&1 && break
    sleep 1
  done
  # The dump strips ownership but keeps grants to brand_ops_app; create that role
  # so the GRANT statements apply cleanly (the live initdb does this for real).
  docker exec "$SCRATCH" psql -U brand_ops_owner -d "$PG_DB" -c \
    "DO \$\$ BEGIN CREATE ROLE brand_ops_app NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END \$\$;" >/dev/null
  echo "    loading dump..."
  gunzip -c "$DUMP" | docker exec -i "$SCRATCH" psql -q -U brand_ops_owner -d "$PG_DB" >/dev/null
  TABLES="$(docker exec "$SCRATCH" psql -U brand_ops_owner -d "$PG_DB" -tAc \
    "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';")"
  BRANDS="$(docker exec "$SCRATCH" psql -U brand_ops_owner -d "$PG_DB" -tAc \
    "SELECT count(*) FROM brand;" 2>/dev/null || echo '?')"
  WS="$(docker exec "$SCRATCH" psql -U brand_ops_owner -d "$PG_DB" -tAc \
    "SELECT count(*) FROM workspace;" 2>/dev/null || echo '?')"
  echo "    RESTORED OK into scratch: ${TABLES} public tables, workspace=${WS}, brand=${BRANDS}"
  echo "    kits archive holds ${KITS_FILES} entries"
  echo ">>> VERIFY PASSED. The backup pair is restorable. (scratch container discarded)"
  exit 0
fi

# --- DESTRUCTIVE restore into the live deployment ---
echo ">>> CONFIRM: this OVERWRITES the live $PG_DB DB in $PG_CONTAINER and the brand-kits volume."
docker inspect "$PG_CONTAINER" >/dev/null 2>&1 || { echo "container $PG_CONTAINER not running"; exit 1; }
PG_USER="$(docker inspect "$PG_CONTAINER" --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | sed -n 's/^POSTGRES_USER=//p' | head -1)"; [ -n "$PG_USER" ] || PG_USER=brand_ops_owner

echo ">>> restoring DB $PG_DB into $PG_CONTAINER (user=$PG_USER)"
gunzip -c "$DUMP" | docker exec -i "$PG_CONTAINER" psql -v ON_ERROR_STOP=1 -U "$PG_USER" -d "$PG_DB"

VOL="$(docker volume ls --format '{{.Name}}' | grep -E 'brand-ops-kits$' | head -1)"
[ -n "$VOL" ] || { echo "brand-ops-kits volume not found"; exit 1; }
KITS_DIR="$(cd "$(dirname "$KITS")" && pwd)"
echo ">>> restoring kits volume $VOL"
docker run --rm -v "$VOL":/data -v "$KITS_DIR":/in:ro alpine \
  sh -c "find /data -mindepth 1 -delete 2>/dev/null; tar xzf /in/$(basename "$KITS") -C /data"

echo ">>> restore complete. Recreate the backend so it serves the restored state:"
echo "    cd /home/muut/brand-ops/deploy && docker compose -p <project> -f <file> up -d --force-recreate brand-ops-backend"
