# s3-folder-sync

Sync a local folder to S3-compatible object storage across multiple machines. Designed for keeping Obsidian vaults, notes, dotfiles, or any file-based workspace in sync without a third-party sync service.

```
┌─────────┐       ┌─────────────┐       ┌─────────┐
│  Mac 1  │──────▶│  S3 Bucket  │◀──────│  Mac 2  │
│ (watch) │◀──────│  (source of │──────▶│ (watch) │
└─────────┘       │   truth)    │       └─────────┘
                  └─────────────┘
```

## Features

- **Real-time sync** — watches for file changes with debounce for rapid saves (Obsidian-friendly)
- **Conflict resolution** — both sides edited? Remote wins as canonical, local saved as `.conflict.*` file. No data is ever lost. Conflict files are local-only and never synced.
- **Soft deletes** — deleted files go to a local trash folder with a configurable grace period before propagating
- **Multiple backends** — supports S3-compatible storage (AWS S3, Cloudflare R2, MinIO, Backblaze B2) and Bunny.net Edge Storage natively
- **Background daemon** — runs as a background process, syncs every 10 seconds by default
- **Menu bar app** — optional macOS menu bar icon for at-a-glance status (requires `rumps`)
- **Ignore patterns** — skip `.git/`, `node_modules/`, `.DS_Store`, etc.

## Install

```bash
git clone https://github.com/gayanhewa/s3-folder-sync.git
cd s3-folder-sync
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# Optional: menu bar support
pip install -e ".[menubar]"
```

Or install directly with pipx (no clone needed):

```bash
pipx install git+https://github.com/gayanhewa/s3-folder-sync.git
```

Requires Python 3.11+.

## CLI Reference

### `s3-folder-sync init`

Initialize sync configuration for a directory. Creates a `.s3sync/` folder containing `config.toml` and sets up the storage connection.

```bash
s3-folder-sync init \
  --path ~/Workspace \
  --endpoint https://s3.amazonaws.com \
  --bucket my-sync-bucket \
  --region us-east-1 \
  --access-key "YOUR_ACCESS_KEY" \
  --secret-key "YOUR_SECRET_KEY" \
  --machine-id mac-1 \
  --backend s3
```

| Flag | Description |
|------|-------------|
| `--path` | Directory to sync (default: current directory) |
| `--endpoint` | Storage endpoint URL |
| `--bucket` | Bucket or storage zone name |
| `--prefix` | Optional key prefix within the bucket |
| `--region` | Storage region (default: `us-east-1`) |
| `--access-key` | Access key / API key |
| `--secret-key` | Secret key / API password |
| `--machine-id` | Unique identifier for this machine (default: hostname) |
| `--backend` | Storage backend: `s3` or `bunny` (default: `s3`) |

### `s3-folder-sync start`

Start the sync daemon. Watches the folder for changes and syncs every 10 seconds (configurable).

```bash
# Foreground (logs to terminal, Ctrl+C to stop)
s3-folder-sync start --path ~/Workspace

# Background (detaches from terminal, writes to .s3sync/daemon.log)
s3-folder-sync start --path ~/Workspace -d
```

| Flag | Description |
|------|-------------|
| `--path` | Synced directory |
| `-d` | Run as background daemon |

**Foreground vs background:**

- **Foreground** (`start`) — the process runs in your terminal. You see logs in real time. Press `Ctrl+C` to stop. Use this when testing or debugging.
- **Background** (`start -d`) — the process detaches from the terminal and runs silently. Logs go to `.s3sync/daemon.log`. The process survives closing the terminal. Use `s3-folder-sync stop` to shut it down. Use this for day-to-day operation.

Both modes do exactly the same syncing. The only difference is where the process runs and where logs go.

### `s3-folder-sync stop`

Stop a running background daemon.

```bash
s3-folder-sync stop --path ~/Workspace
```

Has no effect on foreground processes (use `Ctrl+C` for those).

### `s3-folder-sync status`

Show current sync status: machine ID, bucket, whether the daemon is running, and how many files are tracked.

```bash
s3-folder-sync status --path ~/Workspace
```

Example output:

```
Watch path: /Users/you/Workspace
Machine ID: mac-1
Bucket: my-sync-bucket
Daemon: running (PID 12345)
Tracked files: 42 synced, 0 pending delete
```

### `s3-folder-sync sync`

Force a single sync cycle immediately, then exit. Useful for one-off syncs or cron jobs.

```bash
s3-folder-sync sync --path ~/Workspace
```

### `s3-folder-sync conflicts`

List or clean conflict files. Conflict files are created when the same file is edited on two machines before either syncs. The remote version becomes the canonical file, and the local version is saved as `<name>.conflict.<machine-id>.<timestamp>.<ext>`.

```bash
# List conflict files
s3-folder-sync conflicts --path ~/Workspace

# Delete all conflict files
s3-folder-sync conflicts --clean --path ~/Workspace
```

Conflict files are local-only — they are never synced to remote storage.

### `s3-folder-sync menubar`

Launch a macOS menu bar app showing sync status. Requires the `menubar` extra (`pip install -e ".[menubar]"`).

```bash
s3-folder-sync menubar --path ~/Workspace
```

### Global flags

| Flag | Description |
|------|-------------|
| `-v` / `--verbose` | Enable debug logging |

## Important: First Sync on a New Machine

When setting up a second (or third) machine, always run a one-off `sync` before starting the daemon:

```bash
s3-folder-sync init --path ~/Workspace ...
s3-folder-sync sync --path ~/Workspace   # pull all existing files first
s3-folder-sync start --path ~/Workspace  # then start the daemon
```

If you skip the initial `sync` and jump straight to `start`, the daemon will still pull everything on its first cycle. However, if the folder already contains files (e.g. a partial copy), those files may be detected as conflicts. Running `sync` first ensures a clean baseline before the daemon takes over.

## How Sync Works

Each sync cycle:

1. **Scan local** — walk the directory, hash every file, compare against last-known state in SQLite
2. **List remote** — query storage for current objects and their metadata
3. **Diff** — compare local state, remote state, and last-synced state to determine what changed
4. **Resolve and execute:**

| Scenario | Action |
|----------|--------|
| New local file | Push to remote |
| New remote file | Pull to local |
| Local edit only | Push to remote |
| Remote edit only | Pull to local |
| Both edited (same content) | No-op |
| Both edited (different content) | **Conflict** — pull remote as canonical, save local as `.conflict.*` |
| Deleted locally | Schedule remote delete (after grace period) |
| Deleted remotely | Move local to `.s3sync/trash/<date>/` |
| One side edited, other deleted | Edit wins (safer) |

## Configuration

Stored in `<watch-path>/.s3sync/config.toml`:

```toml
[storage]
endpoint = "https://syd.storage.bunnycdn.com"
bucket = "my-zone"
prefix = ""
region = "syd"
access_key = "your-key"
secret_key = "your-key"
backend = "bunny"   # "s3" or "bunny"

[sync]
interval = 10          # seconds between sync cycles
debounce = 2.0         # seconds to wait after file change before syncing
delete_grace_period = 300  # seconds before propagating deletes to remote

[machine]
id = "mac-1"           # unique per machine, used in conflict filenames

[ignore]
patterns = [
  ".DS_Store",
  "*.tmp",
  ".git/**",
  "node_modules/**",
  ".s3sync/**",
  "*.conflict.*",
]
```

## Local Testing Guide

You can verify bidirectional sync on a single machine by using two separate folders that act as two "machines".

### 1. Set up storage

Use any S3-compatible service, or for fully local testing, run [MinIO](https://min.io/):

```bash
# Option A: Use an existing bucket (AWS, Bunny, R2, etc.)
# Option B: Run MinIO locally
docker run -p 9000:9000 -p 9001:9001 \
  -e MINIO_ROOT_USER=minioadmin \
  -e MINIO_ROOT_PASSWORD=minioadmin \
  minio/minio server /data --console-address ":9001"

# Create a bucket via the MinIO console at http://localhost:9001
# or with: aws --endpoint-url http://localhost:9000 s3 mb s3://test-sync
```

### 2. Create two folders

```bash
mkdir -p ~/test-sync/machine-1
mkdir -p ~/test-sync/machine-2
```

### 3. Initialize both with the same bucket, different machine IDs

```bash
# Machine 1
s3-folder-sync init \
  --path ~/test-sync/machine-1 \
  --endpoint http://localhost:9000 \
  --bucket test-sync \
  --access-key minioadmin \
  --secret-key minioadmin \
  --machine-id machine-1 \
  --backend s3

# Machine 2
s3-folder-sync init \
  --path ~/test-sync/machine-2 \
  --endpoint http://localhost:9000 \
  --bucket test-sync \
  --access-key minioadmin \
  --secret-key minioadmin \
  --machine-id machine-2 \
  --backend s3
```

### 4. Start both daemons (use two terminals)

```bash
# Terminal 1
s3-folder-sync start --path ~/test-sync/machine-1

# Terminal 2
s3-folder-sync start --path ~/test-sync/machine-2
```

### 5. Test sync

```bash
# Create a file on machine-1
echo "hello from machine 1" > ~/test-sync/machine-1/test.md

# Wait ~10 seconds, then check machine-2
cat ~/test-sync/machine-2/test.md
# Output: hello from machine 1

# Edit on machine-2
echo "edited on machine 2" >> ~/test-sync/machine-2/test.md

# Wait ~10 seconds, then check machine-1
cat ~/test-sync/machine-1/test.md
# Output: hello from machine 1
#         edited on machine 2
```

### 6. Test conflict resolution

```bash
# Stop both daemons (Ctrl+C in each terminal)

# Edit the same file on both sides
echo "version A" > ~/test-sync/machine-1/test.md
echo "version B" > ~/test-sync/machine-2/test.md

# Sync machine-1 first (pushes "version A" to remote)
s3-folder-sync sync --path ~/test-sync/machine-1

# Sync machine-2 (detects conflict)
s3-folder-sync sync --path ~/test-sync/machine-2
# machine-2/test.md now contains "version A" (remote wins)
# machine-2/test.md.conflict.machine-2.<timestamp>.md contains "version B"

# List and clean conflicts
s3-folder-sync conflicts --path ~/test-sync/machine-2
s3-folder-sync conflicts --clean --path ~/test-sync/machine-2
```

---

## Runbook: Setting Up with Bunny.net

Bunny.net offers Edge Storage with a native REST API. This tool has a built-in Bunny backend (`--backend bunny`) that works with it directly.

### 1. Create a Storage Zone

1. Log in to [bunny.net dashboard](https://dash.bunny.net)
2. Go to **Storage** > **Add Storage Zone**
3. Name it (e.g. `workspace-sync`)
4. Select your **primary region** (pick the one closest to you)

### 2. Get Your Credentials

1. Go to **Storage** > select your zone > **FTP & API Access**
2. Note the **Password** — this is your access key
3. Note the **Hostname** — this is your endpoint

| Region | Endpoint |
|--------|----------|
| Falkenstein (EU) | `storage.bunnycdn.com` |
| New York (US) | `ny.storage.bunnycdn.com` |
| Los Angeles (US) | `la.storage.bunnycdn.com` |
| Singapore (SG) | `sg.storage.bunnycdn.com` |
| Sydney (AU) | `syd.storage.bunnycdn.com` |
| London (UK) | `uk.storage.bunnycdn.com` |

### 3. Initialize and start

```bash
s3-folder-sync init \
  --path ~/Workspace \
  --endpoint https://syd.storage.bunnycdn.com \
  --bucket your-zone-name \
  --access-key "your-storage-password" \
  --secret-key "your-storage-password" \
  --machine-id mac-1 \
  --backend bunny

# Test with a foreground run first
s3-folder-sync start --path ~/Workspace

# Once confirmed working, run in background
s3-folder-sync start --path ~/Workspace -d
```

### 4. Set up the second machine

Same steps, different `--machine-id`:

```bash
s3-folder-sync init \
  --path ~/Workspace \
  --endpoint https://syd.storage.bunnycdn.com \
  --bucket your-zone-name \
  --access-key "your-storage-password" \
  --secret-key "your-storage-password" \
  --machine-id mac-2 \
  --backend bunny

# Pull existing files, then start daemon
s3-folder-sync sync --path ~/Workspace
s3-folder-sync start --path ~/Workspace -d
```

### 5. Run on login (macOS)

Create a Launch Agent to start automatically:

```bash
cat > ~/Library/LaunchAgents/com.s3foldersync.plist << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.s3foldersync</string>
    <key>ProgramArguments</key>
    <array>
        <string>$(which s3-folder-sync)</string>
        <string>start</string>
        <string>--path</string>
        <string>$HOME/Workspace</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/s3-folder-sync.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/s3-folder-sync.err</string>
</dict>
</plist>
EOF

launchctl load ~/Library/LaunchAgents/com.s3foldersync.plist
```

### Troubleshooting

| Problem | Solution |
|---------|----------|
| "No config found" | Run `s3-folder-sync init` in the target directory first |
| Auth errors | Verify your storage zone password in the Bunny dashboard |
| Files not syncing | Check `s3-folder-sync status`, run with `-v` for debug logs |
| Conflict files appearing | Both machines edited the same file. Review `.conflict.*` files, keep what you want, then `conflicts --clean` |
| Daemon won't start | Check if one is already running: `s3-folder-sync status` |

## Development

```bash
git clone https://github.com/gayanhewa/s3-folder-sync.git
cd s3-folder-sync
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/ -v
```

## License

MIT
