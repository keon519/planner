#!/usr/bin/env bash
#
# Restore the planner database from a backup.
#
#   ~/planner/restore.sh --list              show what's available
#   ~/planner/restore.sh Tuesday             restore from a local weekday copy
#   ~/planner/restore.sh --remote Tuesday    pull from the cloud first
#
# The current database is never deleted — it's moved aside with a timestamp
# so a bad restore is itself reversible.

set -euo pipefail

PLANNER_DIR="${PLANNER_DIR:-$HOME/planner}"
[ -f "$PLANNER_DIR/backup.env" ] && . "$PLANNER_DIR/backup.env"

REMOTE="${PLANNER_REMOTE:-}"
DB="$PLANNER_DIR/data/planner.db"
BACKUP_DIR="$PLANNER_DIR/data/backups"
SERVICE="planner"

log() { echo "[restore] $*"; }
die() { echo "[restore] ERROR: $*" >&2; exit 1; }

if [ "${1:-}" = "--list" ] || [ -z "${1:-}" ]; then
    echo "Local backups:"
    ls -lh "$BACKUP_DIR"/planner-*.db 2>/dev/null | awk '{print "  " $9 "  " $5 "  " $6, $7, $8}' \
        || echo "  none"
    if [ -n "$REMOTE" ] && command -v rclone >/dev/null; then
        echo
        echo "Remote backups ($REMOTE/db):"
        rclone lsl "$REMOTE/db/" 2>/dev/null | awk '{print "  " $4 "  " $1 " bytes  " $2}' \
            || echo "  unreachable"
    fi
    echo
    echo "Usage: $0 [--remote] <Monday|Tuesday|...|monthly-2026-08>"
    exit 0
fi

FROM_REMOTE=false
if [ "$1" = "--remote" ]; then FROM_REMOTE=true; shift; fi
WHICH="${1:?which backup? try --list}"
SRC="$BACKUP_DIR/planner-$WHICH.db"

if $FROM_REMOTE; then
    [ -n "$REMOTE" ] || die "PLANNER_REMOTE not set"
    log "pulling planner-$WHICH.db from $REMOTE"
    mkdir -p "$BACKUP_DIR"
    rclone copy "$REMOTE/db/planner-$WHICH.db" "$BACKUP_DIR/" --retries 3 \
        || die "download failed"
fi

[ -f "$SRC" ] || die "no backup at $SRC — run --list"

# Verify the source before touching anything live.
CHECK=$(sqlite3 "$SRC" "PRAGMA integrity_check;" 2>&1 || echo unreadable)
[ "$CHECK" = "ok" ] || die "backup is corrupt: $CHECK"

A=$(sqlite3 "$SRC" "SELECT COUNT(*) FROM assignments;")
S=$(sqlite3 "$SRC" "SELECT COUNT(*) FROM subtasks;")
C=$(sqlite3 "$SRC" "SELECT COUNT(*) FROM classes;")
MOD=$(date -r "$SRC" "+%a %d %b %H:%M")
echo
echo "  Restore from: planner-$WHICH.db  ($MOD)"
echo "  Contains:     $C classes, $A assignments, $S steps"
echo
read -rp "  This replaces your current database. Continue? [y/N] " ans
[ "$ans" = "y" ] || { log "cancelled"; exit 0; }

log "stopping $SERVICE"
sudo systemctl stop "$SERVICE" || true

# Move the current database aside rather than deleting it.
if [ -f "$DB" ]; then
    ASIDE="$DB.replaced-$(date +%Y%m%d-%H%M%S)"
    mv "$DB" "$ASIDE"
    rm -f "$DB-wal" "$DB-shm"
    log "previous database kept at $(basename "$ASIDE")"
fi

cp "$SRC" "$DB"
sqlite3 "$DB" "PRAGMA journal_mode = WAL;" >/dev/null

log "starting $SERVICE"
sudo systemctl start "$SERVICE"
sleep 3
if curl -sf localhost:8000/api/health >/dev/null; then
    log "restored and healthy"
else
    log "service did not come back — check: journalctl -u $SERVICE -n 30"
    exit 1
fi
