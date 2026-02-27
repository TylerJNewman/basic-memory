#!/bin/bash
# Ensure config and data directories exist and are writable
# Railway volumes mount as root, so we need to create subdirs
mkdir -p "${BASIC_MEMORY_CONFIG_DIR:-/app/data/.config}"
mkdir -p "${BASIC_MEMORY_HOME:-/app/data/shared}"

# --- R2 Bisync Loop ---
# Trigger: RCLONE_CONFIG_R2_TYPE is set (rclone env var config present)
# Why: enables bidirectional file sync between Railway and Cloudflare R2
# Outcome: background loop runs bisync every SYNC_INTERVAL seconds
if command -v rclone &>/dev/null && [ -n "$RCLONE_CONFIG_R2_TYPE" ]; then
  SYNC_INTERVAL="${BM_SYNC_INTERVAL:-300}"
  SYNC_BUCKET="${BM_SYNC_BUCKET:-bm-sync}"
  SYNC_FILTER="/app/sync-filter.txt"

  # Persist bisync state across deploys on the Railway volume
  export RCLONE_CACHE_DIR="/app/data/.cache/rclone"
  mkdir -p "$RCLONE_CACHE_DIR/bisync"

  # Establish bisync baseline if no state exists yet
  if [ -z "$(ls -A "$RCLONE_CACHE_DIR/bisync/" 2>/dev/null)" ]; then
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
