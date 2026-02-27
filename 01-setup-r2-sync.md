# Phase 01: Set Up Cloudflare R2 Sync Between Local and Railway

## Goal

Establish bidirectional file sync between your local Basic Memory and the Railway sidecar, using Cloudflare R2 as the sync hub. After this phase, changes on either side propagate to the other via R2.

## Current Setup Reference

| Component | Value |
|-----------|-------|
| **Railway project** | `exemplary-renewal` |
| **Railway service** | `bm-sync` |
| **Railway URL** | `https://robust-creation-production-70db.up.railway.app` |
| **MCP endpoint** | `https://robust-creation-production-70db.up.railway.app/mcp` |
| **Transport** | Streamable HTTP (POST /mcp) |
| **Dockerfile** | `Dockerfile.sidecar` |
| **Deploy repo** | `quantafinance/bm-sync` (auto-deploys on push to main) |
| **Railway volume** | 50GB at `/app/data` |
| **Railway files** | `/app/data/shared/` (markdown), `/app/data/.config/` (db + config) |
| **Local project** | `/Users/tyler/basic-memory` (project "main") |
| **Local config** | `~/.basic-memory/config.json` |

## Prerequisites

- Phase 00 complete: Railway sidecar running with persistent volume at `/app/data`
- MCP server verified: Streamable HTTP transport working at `/mcp`
- Cloudflare account (free tier is sufficient)
- rclone installed locally (`brew install rclone` on macOS)
- Local Basic Memory project at `~/basic-memory` with markdown files

---

## Step 1: Create Cloudflare R2 Bucket

1. Go to Cloudflare dashboard → R2 Object Storage
2. Create a bucket:
   - Name: `bm-sync`
   - Location: Auto (or nearest region)
3. Create an API token:
   - R2 dashboard → Manage R2 API Tokens → Create API Token
   - Permissions: Object Read & Write
   - Scope: Apply to specific bucket → `basic-memory-sync`
4. Save these values:
   - **Account ID** (from Cloudflare dashboard URL or overview page)
   - **Access Key ID** (from the API token creation)
   - **Secret Access Key** (from the API token creation)

### Cost verification

R2 free tier includes:
- 10 GB storage/month
- 1 million Class B (read) operations/month
- 10 million Class A (write) operations/month
- **$0 egress always**

For markdown files from a few users, this is effectively free indefinitely.

---

## Step 2: Configure rclone Locally

```bash
# Check rclone is installed
rclone --version

# If not installed:
# macOS: brew install rclone
# Linux: sudo apt install rclone  (or curl https://rclone.org/install.sh | sudo bash)
```

Create the rclone remote configuration:

```bash
rclone config

# Interactive setup:
# n) New remote
# name> r2
# Storage> s3
# provider> Cloudflare
# access_key_id> <your R2 Access Key ID>
# secret_access_key> <your R2 Secret Access Key>
# endpoint> https://<ACCOUNT_ID>.r2.cloudflarestorage.com
# (accept defaults for everything else)
```

Or write the config directly:

```bash
cat >> ~/.config/rclone/rclone.conf << 'EOF'
[r2]
type = s3
provider = Cloudflare
access_key_id = <YOUR_ACCESS_KEY_ID>
secret_access_key = <YOUR_SECRET_ACCESS_KEY>
endpoint = https://<ACCOUNT_ID>.r2.cloudflarestorage.com
acl = private
no_check_bucket = true
EOF
```

### Verify local rclone connectivity:

```bash
# List bucket contents (should be empty)
rclone ls r2:bm-sync

# Write a test file
echo "test" | rclone rcat r2:bm-sync/test.txt

# Read it back
rclone cat r2:bm-sync/test.txt

# Clean up
rclone delete r2:bm-sync/test.txt
```

---

## Step 3: Install rclone on Railway Sidecar

The existing Dockerfile does NOT include rclone. Two options:

### Option A: Modify the Dockerfile (recommended if you control the image)

Add to the Dockerfile after the system deps:

```dockerfile
# Install rclone for R2 sync
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl unzip \
    && curl -O https://downloads.rclone.org/current/rclone-current-linux-amd64.zip \
    && unzip rclone-current-linux-amd64.zip \
    && cp rclone-*-linux-amd64/rclone /usr/local/bin/ \
    && rm -rf rclone-* \
    && apt-get purge -y curl unzip \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*
```

### Option B: Install at runtime (if you can't modify the image)

In Railway, add a start command that installs rclone before starting Basic Memory:

```bash
# Railway custom start command:
apt-get update && apt-get install -y rclone && basic-memory mcp --transport streamable-http --host 0.0.0.0 --port 8000
```

This is slower (installs on every deploy) but doesn't require a custom image.

### Configure rclone on Railway

Set Railway environment variables for rclone config (avoids needing a config file):

```
RCLONE_CONFIG_R2_TYPE=s3
RCLONE_CONFIG_R2_PROVIDER=Cloudflare
RCLONE_CONFIG_R2_ACCESS_KEY_ID=<your R2 Access Key ID>
RCLONE_CONFIG_R2_SECRET_ACCESS_KEY=<your R2 Secret Access Key>
RCLONE_CONFIG_R2_ENDPOINT=https://<ACCOUNT_ID>.r2.cloudflarestorage.com
RCLONE_CONFIG_R2_ACL=private
RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true
```

rclone reads `RCLONE_CONFIG_<REMOTE>_<KEY>` env vars automatically — no config file needed.

### Verify rclone on Railway:

```bash
# SSH into Railway or use railway run:
rclone ls r2:bm-sync
# Should show empty or test files from Step 2
```

---

## Step 4: Create Sync Filter File

Create a filter file that both sides use to exclude non-markdown files:

```bash
# On local machine, create the filter file
cat > ~/.config/rclone/bm-sync-filter.txt << 'EOF'
# Exclude database and config (each side maintains its own)
- .basic-memory/**
- .obsidian/**
- .git/**
- **/.DS_Store
- **/*.pyc
- **/__pycache__/**
- **/*.tmp
- **/*.swp
- **/*.swo
EOF
```

On Railway, store the same filter content. Either:
- Bake it into the Docker image
- Store in the persistent volume at `/app/data/.sync-filter.txt`
- Or pass excludes as env var / command flags

---

## Step 5: Initial Sync — Local to R2

Push your local markdown files to R2 first (establishes R2 as the shared baseline):

```bash
# Dry run first — see what would be synced
rclone sync ~/basic-memory r2:bm-sync/shared \
  --filter-from ~/.config/rclone/bm-sync-filter.txt \
  --dry-run -v

# If the output looks correct, do the real sync
rclone sync ~/basic-memory r2:bm-sync/shared \
  --filter-from ~/.config/rclone/bm-sync-filter.txt \
  -v
```

Verify:

```bash
rclone ls r2:bm-sync/shared
# Should show your markdown files
```

---

## Step 6: Initial Sync — R2 to Railway

Pull files from R2 into the Railway container:

```bash
# On Railway (via SSH or railway run):
rclone sync r2:bm-sync/shared /app/data/shared \
  --filter-from /app/data/.sync-filter.txt \
  -v

# Rebuild the database index from the synced files
basic-memory reset
basic-memory status
# Should show files synced, 0 errors
```

---

## Step 7: Establish Bisync Baseline

rclone bisync requires a one-time `--resync` to create tracking state.

### On local machine:

```bash
# Establish bisync baseline
rclone bisync ~/basic-memory r2:bm-sync/shared \
  --filter-from ~/.config/rclone/bm-sync-filter.txt \
  --create-empty-src-dirs \
  --resync \
  -v

# Verify: check bisync state was created
ls ~/.cache/rclone/bisync/
# Should show state files
```

### On Railway:

```bash
rclone bisync /app/data/shared r2:bm-sync/shared \
  --filter-from /app/data/.sync-filter.txt \
  --create-empty-src-dirs \
  --resync \
  -v
```

---

## Step 8: Test Bidirectional Sync

### Test 1: Local → R2 → Railway

```bash
# On local machine: create a test note
echo "# Test Note\n\n- [test] Created locally" > ~/basic-memory/notes/sync-test-local.md

# Sync local to R2
rclone bisync ~/basic-memory r2:bm-sync/shared \
  --filter-from ~/.config/rclone/bm-sync-filter.txt \
  -v

# Verify on R2
rclone cat r2:bm-sync/shared/notes/sync-test-local.md

# On Railway: sync R2 to container
rclone bisync /app/data/shared r2:bm-sync/shared \
  --filter-from /app/data/.sync-filter.txt \
  -v

# Verify on Railway
cat /app/data/shared/notes/sync-test-local.md
basic-memory tool read-note --identifier "sync-test-local"
```

### Test 2: Railway → R2 → Local

```bash
# On Railway: create a test note via Basic Memory
basic-memory tool write-note \
  --title "Sync Test Remote" \
  --content "- [test] Created on Railway sidecar" \
  --directory "notes"

# Sync Railway to R2
rclone bisync /app/data/shared r2:bm-sync/shared \
  --filter-from /app/data/.sync-filter.txt \
  -v

# On local: sync R2 to local
rclone bisync ~/basic-memory r2:bm-sync/shared \
  --filter-from ~/.config/rclone/bm-sync-filter.txt \
  -v

# Verify locally
cat ~/basic-memory/notes/sync-test-remote.md
```

### Clean up test files:

```bash
rm ~/basic-memory/notes/sync-test-local.md
# Re-sync to propagate deletion
```

---

## Step 9: Automate Sync

### On Railway: cron via supervisor or entrypoint script

Create a sync script on the Railway volume:

```bash
cat > /app/data/.sync.sh << 'SCRIPT'
#!/bin/bash
while true; do
  sleep 300  # 5 minutes
  rclone bisync /app/data/shared r2:bm-sync/shared \
    --filter-from /app/data/.sync-filter.txt \
    --resilient \
    --conflict-resolve newer \
    2>&1 | logger -t bm-r2-sync
done
SCRIPT
chmod +x /app/data/.sync.sh
```

Modify the Docker entrypoint to run both the sync loop and the MCP server:

```dockerfile
# Option: use a custom entrypoint
COPY entrypoint.sh /app/entrypoint.sh
CMD ["/app/entrypoint.sh"]
```

```bash
#!/bin/bash
# entrypoint.sh

# Start R2 sync loop in background (if rclone is configured)
if command -v rclone &>/dev/null && [ -n "$RCLONE_CONFIG_R2_TYPE" ]; then
  echo "Starting R2 sync loop (every 5 minutes)..."
  while true; do
    sleep 300
    rclone bisync /app/data/shared r2:bm-sync/shared \
      --exclude ".basic-memory/**" \
      --exclude ".obsidian/**" \
      --resilient \
      --conflict-resolve newer \
      2>&1 | head -20
  done &
fi

# Start Basic Memory MCP server (foreground)
exec basic-memory mcp --transport streamable-http --host 0.0.0.0 --port 8000
```

### On local machine: launchd (macOS) or manual

For manual sync (simplest):

```bash
# Add to ~/.zshrc or create an alias
alias bm-sync='rclone bisync ~/basic-memory r2:bm-sync/shared \
  --filter-from ~/.config/rclone/bm-sync-filter.txt \
  --resilient --conflict-resolve newer -v'
```

Then just run `bm-sync` before and after working locally.

For automated (macOS launchd):

```xml
<!-- ~/Library/LaunchAgents/com.basicmemory.sync.plist -->
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.basicmemory.sync</string>
    <key>ProgramArguments</key>
    <array>
        <string>/opt/homebrew/bin/rclone</string>
        <string>bisync</string>
        <string>/Users/tyler/basic-memory</string>
        <string>r2:bm-sync/shared</string>
        <string>--filter-from</string>
        <string>/Users/tyler/.config/rclone/bm-sync-filter.txt</string>
        <string>--resilient</string>
        <string>--conflict-resolve</string>
        <string>newer</string>
    </array>
    <key>StartInterval</key>
    <integer>300</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/bm-sync.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/bm-sync.log</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.basicmemory.sync.plist
```

---

## Verification Checklist

- [x] R2 bucket created on Cloudflare (`bm-sync`)
- [x] rclone configured locally with R2 credentials
- [x] rclone connectivity verified (ls, cat, rcat work)
- [x] rclone installed/available on Railway sidecar (v1.73.1)
- [x] rclone configured on Railway (via env vars)
- [x] Filter file created on both sides
- [x] Initial sync: local files pushed to R2
- [x] Initial sync: R2 files pulled to Railway
- [x] Bisync baseline established on both sides (--resync)
- [x] Test: local note appears on Railway after sync
- [x] Test: Railway note appears locally after sync
- [x] Basic Memory file watcher detects synced files (check `bm status`)
- [x] Automated sync running on Railway (cron/loop)
- [x] Local sync working (manual alias + launchd)

## Conflict Behavior

- **One side changed**: File copies to the other side (normal)
- **Both sides changed same file**: rclone keeps both, loser gets `.conflict1` suffix
- **File deleted on one side**: Deletion propagates to the other side
- **`--conflict-resolve newer`**: Most recent timestamp wins, loser renamed

With a few users and 5-minute sync intervals, conflicts should be extremely rare. If one occurs, check for `.conflict` files and merge manually.

## Troubleshooting

**"bisync not found"**: rclone version too old. Need v1.58+ for bisync, v1.64+ for `--create-empty-src-dirs`. Update rclone.

**"Failed to bisync: must use --resync"**: Bisync state is corrupted or missing. Run with `--resync` to re-establish baseline (safe, just recalculates state).

**"Access Denied" on R2**: Check API token permissions (needs Object Read & Write), check bucket name matches, check account ID in endpoint URL.

**Files sync but Basic Memory doesn't see them**: The file watcher (`BASIC_MEMORY_SYNC_CHANGES=true`) should detect changes. If not, run `basic-memory reset` to force reindex. Check that files land in the correct project directory.

**R2 shows files but Railway is empty**: Verify the rclone remote path matches. `r2:bm-sync/shared` must map to the Railway mount at `/app/data/shared`.

---

## Confidence Level: 90%

rclone bisync with R2 is well-documented and standard. The main uncertainties:
1. Railway's ability to run background processes alongside the main CMD (the entrypoint.sh approach should work but needs testing)
2. rclone env var config (`RCLONE_CONFIG_R2_*`) — well-documented but worth verifying on Railway specifically
3. File watcher picking up rclone-synced files — should work since watchfiles uses OS-level notifications, but bulk file drops might need a delay

If any of these don't work as expected: stop, investigate, try alternative approaches before proceeding.

---

## Detailed Task Breakdown

### Phase A: Cloudflare R2 Setup
- [x] **A1.** Log in to Cloudflare dashboard
- [x] **A2.** Navigate to R2 Object Storage → Create bucket named `bm-sync`
- [x] **A3.** Create R2 API token (Object Read & Write, scoped to `bm-sync` bucket)
- [x] **A4.** Record Account ID, Access Key ID, and Secret Access Key securely
- [x] **A5.** Verify R2 free tier limits are acceptable (10GB storage, $0 egress)

### Phase B: Local rclone Configuration
- [x] **B1.** Install rclone locally: `brew install rclone` (v1.73.1 installed)
- [x] **B2.** Verify rclone version ≥ 1.64 (needed for bisync + `--create-empty-src-dirs`)
- [x] **B3.** Configure rclone remote `r2` with R2 credentials (via direct config write to `~/.config/rclone/rclone.conf`)
- [x] **B4.** Verify connectivity: `rclone ls r2:bm-sync` (empty bucket confirmed)
- [x] **B5.** Test write/read cycle: `rclone rcat` → `rclone cat` → `rclone delete` (all passed)

### Phase C: Railway Sidecar rclone Setup
- [x] **C1.** Add rclone install to `Dockerfile.sidecar` (after `uv sync`, before `mkdir`) — pinned v1.73.1
- [x] **C2.** Push Dockerfile change to `quantafinance/bm-sync` → Railway redeploy succeeded
- [x] **C3.** Set Railway environment variables for rclone config (all 7 RCLONE_CONFIG_R2_* vars set via `railway variables --set`)
- [x] **C4.** SSH into Railway and verified: `rclone ls r2:bm-sync/` works (empty bucket, no errors)
- [x] **C5.** MCP server still responding at /mcp after Dockerfile change (HTTP 200)

### Phase D: Sync Filter Configuration
- [x] **D1.** Create local filter file at `~/.config/rclone/bm-sync-filter.txt`
- [x] **D2.** Add filter file to repo as `sync-filter.txt` (baked into image at `/app/sync-filter.txt` via `ADD . /app`)
- [x] **D3.** Verified both filter files are identical (diff confirms)

### Phase E: Initial One-Way Sync
- [x] **E1.** Dry run local → R2: 4 markdown files (~38KB) identified
- [x] **E2.** Reviewed dry run output — only markdown files, no excluded items
- [x] **E3.** Executed local → R2 sync: 4 files copied successfully
- [x] **E4.** Verified files on R2: `rclone ls r2:bm-sync/shared` shows all 4 files
- [x] **E5.** SSH into Railway, pulled R2 → Railway: 4 files synced, old test notes cleaned up
- [x] **E6.** Railway file watcher auto-indexed (status shows "No changes" — already up to date)
- [x] **E7.** Verified via `basic-memory tool search-notes "architecture"` — files indexed and searchable

### Phase F: Establish Bisync Baseline
- [x] **F1.** Ran bisync `--resync` on local — baseline established, follow-up bisync also succeeded
- [x] **F2.** Local bisync state verified (rclone manages internally, follow-up bisync worked without --resync)
- [x] **F3.** Ran bisync `--resync` on Railway — baseline established successfully
- [x] **F4.** Railway bisync state at `/root/.cache/rclone/bisync/` (ephemeral — will need persistence in Phase H)

### Phase G: Bidirectional Sync Testing
- [x] **G1.** Local → R2 → Railway: Created `sync-test-local.md` locally, bisync'd to R2, bisync'd to Railway, verified via `read-note` — all passed
- [x] **G2.** Railway → R2 → local: Created `Sync Test Remote.md` on Railway, bisync'd to R2, bisync'd locally — arrived with full content
- [x] **G3.** Deletion propagation: Deleted `sync-test-local.md` locally, bisync chain propagated deletion to R2 and Railway — confirmed
- [ ] **G4.** *(Skipped)* Conflict handling — `--conflict-resolve newer` is well-documented rclone behavior, will test if issues arise
- [x] **G5.** All test files cleaned up from local, R2, and Railway

### Phase H: Automate Railway Sync
- [x] **H1.** Updated `entrypoint.sh` with background bisync loop before `exec "$@"`
- [x] **H2.** Conditional on `command -v rclone` and `RCLONE_CONFIG_R2_TYPE` being set
- [x] **H3.** Configurable via `BM_SYNC_INTERVAL` env var (default 300s), `BM_SYNC_BUCKET` (default `bm-sync`)
- [x] **H4.** Added `--resilient` and `--conflict-resolve newer` flags
- [x] **H5.** Bisync state persisted via symlink: `/root/.cache/rclone` → `/app/data/.cache/rclone` (volume). Auto-runs `--resync` baseline if no state exists.
- [x] **H6.** Pushed to `quantafinance/bm-sync`, deployed successfully
- [x] **H7.** Logs show `[bm-sync] Running bisync...` / `[bm-sync] Bisync complete.` at configured interval
- [x] **H8.** MCP server responds HTTP 200 during sync cycles

### Phase I: Automate Local Sync
- [x] **I1.** Added `bm-sync` alias to `~/.zshrc`
- [x] **I2.** Created macOS launchd plist at `~/Library/LaunchAgents/com.basicmemory.sync.plist` (5-minute interval)
- [x] **I3.** Loaded plist via `launchctl load` — job running (exit code 0)
- [x] **I4.** Verified: manual run shows "Bisync successful", launchd job runs every 5 min

### Phase J: End-to-End Verification
- [x] **J1.** Local note `e2e-verification.md` appeared on Railway via automated sync (local → R2 → Railway)
- [x] **J2.** Railway note `e2e-from-railway.md` appeared locally via automated sync (Railway → R2 → local)
- [x] **J3.** `basic-memory status` on Railway shows "No changes" (healthy)
- [x] **J4.** Full verification checklist completed (see above)
- [x] **J5.** Deviations: rclone `/current/` URL 404s from Railway build (pinned version), bisync ignores `--cache-dir` (used symlink), `railway ssh` splits args on spaces (use `sh -c`)
- [x] **J6.** Written `infrastructure/R2 Sync Configuration.md` to knowledge base
