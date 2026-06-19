#!/usr/bin/env bash
# Brand-Ops backup: dump the brand_ops Postgres AND archive the brand-kits volume
# (the irreplaceable generated assets + brand definitions), verify both, keep a
# local retention window, and push off-host when configured.
#
# Follows the suite pattern (contact-ops-backup.sh / the bigboy pg-backup.sh) with
# two improvements:
#   1. secrets are sourced from a host-only env file, NEVER inlined in this script
#   2. the brand-kits VOLUME is archived too, not just the database
#
# DOGFOOD (bigboy) is already covered host-wide by pg-backup.timer (dumps every
# postgres container) + restic-backup.timer (/var/lib/docker -> Backblaze B2), so
# this script is primarily for the PROD box (commander), which has no host-wide
# backup regime. It is safe to run on either box (it auto-detects the kits volume).
#
# Schedule (cron, prod): 0 3 * * * /home/muut/brand-ops/scripts/backup-brand-ops.sh
# Restore / DR: see scripts/restore-brand-ops.sh and docs/BACKUP.md.
set -euo pipefail

PG_CONTAINER="${BRANDOPS_PG_CONTAINER:-brand-ops-postgres}"
PG_DB="${BRANDOPS_PG_DB:-brand_ops}"
KEEP_DAYS="${BRANDOPS_BACKUP_KEEP_DAYS:-14}"
KEEP_DIR="${BRANDOPS_BACKUP_DIR:-${HOME}/backups/brand-ops}"
LOG="${BRANDOPS_BACKUP_LOG:-${HOME}/logs/brand-ops-backup.log}"
ENV_FILE="${BRANDOPS_BACKUP_ENV:-/home/muut/brand-ops/.backup.env}"

TS="$(date +%Y%m%d_%H%M%S)"
WORK="$(mktemp -d /tmp/brand-ops-backup.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$KEEP_DIR" "$(dirname "$LOG")"
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }
fail() { log "ERROR: $*"; exit 1; }

log "=== brand-ops backup start ($TS) ==="

command -v docker >/dev/null 2>&1 || fail "docker not found"
docker inspect "$PG_CONTAINER" >/dev/null 2>&1 || fail "container $PG_CONTAINER not running"

# Auto-detect the owner role from the container env (like the host pg-backup.sh),
# so the dump runs as whoever owns the schema; fall back to the documented owner.
PG_USER="${BRANDOPS_PG_USER:-$(docker inspect "$PG_CONTAINER" \
  --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | sed -n 's/^POSTGRES_USER=//p' | head -1)}"
[ -n "$PG_USER" ] || PG_USER=brand_ops_owner

# --- 1. Postgres dump (--clean --if-exists so a restore is idempotent; keep the
#        ACL/grants so the NOBYPASSRLS app role keeps its access on restore; roles
#        themselves are recreated by deploy/initdb/01-roles.sql on a fresh pgdata) ---
DUMP="$WORK/brand_ops_${TS}.sql.gz"
log "dumping $PG_DB from $PG_CONTAINER (user=$PG_USER)"
docker exec "$PG_CONTAINER" pg_dump -U "$PG_USER" -d "$PG_DB" \
  --clean --if-exists --no-owner --format=plain 2>>"$LOG" | gzip > "$DUMP"
[ "$(stat -c%s "$DUMP")" -gt 1024 ] || fail "dump suspiciously small; refusing"
gzip -t "$DUMP" 2>/dev/null || fail "dump gzip corrupt; refusing"
log "dump ok: $(du -h "$DUMP" | cut -f1)"

# --- 2. brand-kits volume archive (read it via a throwaway container so we need
#        no host /var/lib/docker access) ---
VOL="$(docker volume ls --format '{{.Name}}' | grep -E 'brand-ops-kits$' | head -1)"
[ -n "$VOL" ] || fail "brand-ops-kits volume not found"
KITS_NAME="brand_kits_${TS}.tar.gz"
KITS="$WORK/$KITS_NAME"
log "archiving volume $VOL"
docker run --rm -v "$VOL":/data:ro -v "$WORK":/out alpine \
  sh -c "tar czf /out/$KITS_NAME -C /data ." 2>>"$LOG"
[ -s "$KITS" ] || fail "kits archive empty; refusing"
tar tzf "$KITS" >/dev/null 2>&1 || fail "kits archive corrupt; refusing"
log "kits archive ok: $(du -h "$KITS" | cut -f1)"

# --- 3. Local retention ---
cp "$DUMP" "$KITS" "$KEEP_DIR/"
log "copied to $KEEP_DIR"
find "$KEEP_DIR" -maxdepth 1 -type f \
  \( -name 'brand_ops_*.sql.gz' -o -name 'brand_kits_*.tar.gz' \) \
  -mtime "+$KEEP_DAYS" -delete
log "pruned local backups older than $KEEP_DAYS days"

# --- 4. Off-host (DR). Optional: needs the host-only env file with restic OR s3
#        creds. Until it exists this is a LOCAL-ONLY backup (survives volume
#        corruption / docker compose down -v, NOT full host loss). ---
if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  set -a; . "$ENV_FILE"; set +a
  if command -v restic >/dev/null 2>&1 && [ -n "${RESTIC_REPOSITORY:-}" ]; then
    log "off-host: restic backup of $KEEP_DIR -> $RESTIC_REPOSITORY"
    if restic backup --tag brand-ops "$KEEP_DIR" >>"$LOG" 2>&1; then
      restic forget --tag brand-ops --keep-daily 7 --keep-weekly 4 \
        --keep-monthly 6 --prune >>"$LOG" 2>&1 || log "WARN: restic forget/prune failed"
      log "off-host restic ok"
    else
      log "WARN: restic off-host failed; local copy preserved"
    fi
  elif command -v aws >/dev/null 2>&1 && [ -n "${BRANDOPS_S3_BUCKET:-}" ]; then
    EP=(); [ -n "${BRANDOPS_S3_ENDPOINT:-}" ] && EP=(--endpoint-url "$BRANDOPS_S3_ENDPOINT")
    log "off-host: aws s3 cp -> $BRANDOPS_S3_BUCKET"
    if aws s3 cp "$KEEP_DIR/$(basename "$DUMP")" "$BRANDOPS_S3_BUCKET/" "${EP[@]}" >>"$LOG" 2>&1 \
      && aws s3 cp "$KEEP_DIR/$KITS_NAME" "$BRANDOPS_S3_BUCKET/" "${EP[@]}" >>"$LOG" 2>&1; then
      log "off-host s3 ok"
    else
      log "WARN: s3 off-host failed; local copy preserved"
    fi
  else
    log "off-host: $ENV_FILE present but no restic repo / s3 bucket set; local-only"
  fi
else
  log "off-host: DISABLED (no $ENV_FILE). LOCAL-ONLY backup. See docs/BACKUP.md to enable DR."
fi

log "=== brand-ops backup done ==="
