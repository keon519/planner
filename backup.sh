#!/usr/bin/env bash
#
# Nightly backup for the study planner.
#
#   ~/planner/backup.sh            normal run
#   ~/planner/backup.sh --local    skip the upload, keep local copies only
#
# Configure the remote in ~/planner/backup.env so switching cloud providers
# later is a one-line change rather than an edit to this script.

set -euo pipefail

PLANNER_DIR="${PLANNER_DIR:-$HOME/planner}"
[ -f "$PLANNER_DIR/backup.env" ] && . "$PLANNER_DIR/backup.env"

REMOTE="${PLANNER_REMOTE:-}"
DB="$PLANNER_DIR/data/planner.db"
BACKUP_DIR="$PLANNER_DIR/data/backups"
UPLOADS="$PLANNER_DIR/data/uploads"
DAY=$(date +%A)
STAMP=$(date +%Y-%m-%d_%H:%M)
LOCAL_ONLY=false
[ "${1:-}" = "--local" ] && LOCAL_ONLY=true

log() { echo "[$(date +%H:%M:%S)] $*"; }
die() { echo "[$(date +%H:%M:%S)] ERROR: $*" >&2; exit 1; }

[ -f "$DB" ] || die "database not found at $DB"
mkdir -p "$BACKUP_DIR"

# --------------------------------------------------------------------------
# 1. Hot backup.
#
# sqlite3 .backup is safe against a live database with WAL enabled. Plain `cp`
# is NOT — it can capture the main file without the matching -wal contents and
# produce a copy that looks fine and isn't.
# --------------------------------------------------------------------------
TARGET="$BACKUP_DIR/planner-$DAY.db"
TMP="$BACKUP_DIR/.in-progress.db"
rm -f "$TMP"

log "backing up to planner-$DAY.db"
sqlite3 "$DB" ".backup '$TMP'" || die "sqlite backup failed"

# --------------------------------------------------------------------------
# 2. Verify BEFORE it replaces anything or goes anywhere.
#
# A corrupt backup silently overwriting a good one is worse than no backup.
# --------------------------------------------------------------------------
CHECK=$(sqlite3 "$TMP" "PRAGMA integrity_check;" 2>&1 || echo "unreadable")
[ "$CHECK" = "ok" ] || die "integrity check failed: $CHECK"

ASSIGN=$(sqlite3 "$TMP" "SELECT COUNT(*) FROM assignments;")
STEPS=$(sqlite3 "$TMP" "SELECT COUNT(*) FROM subtasks;")
CLASSES=$(sqlite3 "$TMP" "SELECT COUNT(*) FROM classes;")
log "verified: $CLASSES classes, $ASSIGN assignments, $STEPS steps"

# Guard against a truncated or reinitialised database quietly replacing a
# good backup. Override with FORCE=1 if you genuinely wiped everything.
if [ -f "$TARGET" ] && [ "${FORCE:-0}" != "1" ]; then
    PREV=$(sqlite3 "$TARGET" "SELECT COUNT(*) FROM assignments;" 2>/dev/null || echo 0)
    if [ "$ASSIGN" -eq 0 ] && [ "$PREV" -gt 0 ]; then
        die "new backup has 0 assignments but last week's had $PREV — refusing. FORCE=1 to override."
    fi
fi

mv "$TMP" "$TARGET"

# Monthly snapshot: weekday rotation only covers 7 days, so corruption that
# goes unnoticed for two weeks would otherwise take everything with it.
if [ "$(date +%d)" = "01" ]; then
    cp "$TARGET" "$BACKUP_DIR/planner-monthly-$(date +%Y-%m).db"
    log "monthly snapshot kept"
fi

# --------------------------------------------------------------------------
# 3. Upload.
# --------------------------------------------------------------------------
if $LOCAL_ONLY; then
    log "--local set, skipping upload"
    exit 0
fi
[ -n "$REMOTE" ] || die "PLANNER_REMOTE not set. See backup.env"
command -v rclone >/dev/null || die "rclone not installed"

log "uploading to $REMOTE"
rclone copy "$BACKUP_DIR" "$REMOTE/db/" \
    --include "planner-*.db" --transfers 2 --checkers 2 --retries 3

# `copy` not `sync` for uploads: sync would delete remote files if the local
# directory were ever emptied by a bug. This directory only ever grows.
if [ -d "$UPLOADS" ] && [ -n "$(ls -A "$UPLOADS" 2>/dev/null)" ]; then
    rclone copy "$UPLOADS" "$REMOTE/uploads/" --transfers 2 --checkers 2 --retries 3
fi

# NOTE: .env is deliberately never uploaded — it holds the API key.

SIZE=$(du -h "$TARGET" | cut -f1)
log "done — $SIZE, $STAMP"
