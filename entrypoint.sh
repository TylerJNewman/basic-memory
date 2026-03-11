#!/bin/bash
# Ensure config and data directories exist and are writable
# Railway volumes mount as root, so we need to create subdirs
CONFIG_DIR="${BASIC_MEMORY_CONFIG_DIR:-/app/data/.config}"
DATA_HOME="${BASIC_MEMORY_HOME:-/app/data/shared}"
PROJECT_NAME="${BASIC_MEMORY_DEFAULT_PROJECT:-main}"

mkdir -p "$CONFIG_DIR"
mkdir -p "$DATA_HOME"

# --- Pre-configure project ---
# Trigger: config.json does not yet exist (fresh deploy or new volume)
# Why: the MCP server needs at least one project registered to function;
#      without it, reconcile_projects_with_config finds nothing to load
# Outcome: creates minimal config.json so the server starts with a valid project
CONFIG_FILE="$CONFIG_DIR/config.json"
if [ ! -f "$CONFIG_FILE" ]; then
  echo "[bm-init] Creating config.json with project '$PROJECT_NAME' -> $DATA_HOME"
  cat > "$CONFIG_FILE" <<EOCFG
{
  "projects": {
    "$PROJECT_NAME": {
      "path": "$DATA_HOME"
    }
  },
  "default_project": "$PROJECT_NAME"
}
EOCFG
fi

# --- R2 Bisync Loop ---
# Trigger: RCLONE_CONFIG_R2_TYPE is set (rclone env var config present)
# Why: enables bidirectional file sync between Railway and Cloudflare R2
# Outcome: background loop runs bisync every SYNC_INTERVAL seconds
if command -v rclone &>/dev/null && [ -n "$RCLONE_CONFIG_R2_TYPE" ]; then
  SYNC_INTERVAL="${BM_SYNC_INTERVAL:-300}"
  SYNC_BUCKET="${BM_SYNC_BUCKET:-bm-sync}"
  SYNC_FILTER="/app/sync-filter.txt"

  # Persist bisync state across deploys on the Railway volume
  # bisync ignores --cache-dir, so symlink ~/.cache/rclone to the volume
  CACHE_DIR="/app/data/.cache/rclone"
  mkdir -p "$CACHE_DIR/bisync"
  mkdir -p /root/.cache
  rm -rf /root/.cache/rclone
  ln -s "$CACHE_DIR" /root/.cache/rclone

  # Establish bisync baseline if no state exists yet
  if [ -z "$(ls -A "$CACHE_DIR/bisync/" 2>/dev/null)" ]; then
    echo "[bm-sync] No bisync state found, running --resync to establish baseline..."
    rclone bisync /app/data/shared "r2:${SYNC_BUCKET}/shared" \
      --filter-from "$SYNC_FILTER" \
      --create-empty-src-dirs \
      --resync \
      2>&1 | head -20
    echo "[bm-sync] Baseline established."
  fi

  echo "[bm-sync] Starting R2 sync loop (every ${SYNC_INTERVAL}s to r2:${SYNC_BUCKET}/shared)..."
  (
    while true; do
      sleep "$SYNC_INTERVAL"
      echo "[bm-sync] Running bisync..."
      rclone bisync /app/data/shared "r2:${SYNC_BUCKET}/shared" \
        --filter-from "$SYNC_FILTER" \
        --create-empty-src-dirs \
        --resilient \
        --conflict-resolve newer \
        2>&1 | head -20
      echo "[bm-sync] Bisync complete."
    done
  ) &
fi

exec "$@"
