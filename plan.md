# s3-folder-sync

## Overview

A CLI tool that syncs a local folder to S3-compatible object storage, enabling two (or more) machines to keep a shared folder in sync. Primary use case: Obsidian vaults, dotfiles, notes — anything file-based.

## Language Choice

**Go** — single static binary, no runtime dependencies, excellent concurrency primitives, strong stdlib for file I/O and HTTP, cross-compiles trivially for macOS (arm64/amd64).

## Architecture

```
┌─────────┐       ┌─────────────┐       ┌─────────┐
│  Mac 1  │──────▶│  S3 Bucket  │◀──────│  Mac 2  │
│ (watch) │◀──────│  (source of │──────▶│ (watch) │
└─────────┘       │   truth)    │       └─────────┘
                  └─────────────┘
```

Each machine runs the daemon. It watches the local folder, pushes changes to S3, and pulls remote changes periodically.

## Sync Algorithm

### Core Concept: Last-Writer-Wins with Conflict Preservation

1. **Local state tracking** — maintain a local SQLite DB (`.s3sync/state.db`) storing:
   - file path (relative)
   - content hash (SHA-256)
   - local mtime
   - last synced S3 ETag
   - last synced timestamp

2. **Remote state** — S3 object metadata:
   - `x-amz-meta-synced-at` — ISO timestamp of when the file was uploaded
   - `x-amz-meta-source-hash` — SHA-256 of content at upload time
   - `x-amz-meta-machine-id` — which machine uploaded it

3. **Sync loop** (runs every N seconds, default 10):
   - **Scan local** — detect new/modified/deleted files vs local state DB
   - **List remote** — S3 ListObjects to detect remote changes vs last known state
   - **Resolve**:
     - File changed locally only → push to S3
     - File changed remotely only → pull from S3
     - File changed both (conflict) → see conflict resolution below
     - File deleted locally only → delete from S3 (with soft-delete grace period)
     - File deleted remotely only → delete locally (move to `.s3sync/trash/`)

### Conflict Resolution

When both local and remote changed since last sync:

1. **If content hashes match** — no-op (same edit on both sides)
2. **If hashes differ** — keep both:
   - Remote version wins as the canonical file (pull it)
   - Local version saved as `<name>.conflict.<machine-id>.<timestamp>.<ext>`
   - Log the conflict for user review
3. **Deletions vs edits** — edit always wins over delete (safer for data preservation)

### Soft Deletes

- Deleted files go to `.s3sync/trash/<date>/` locally before being removed
- S3 deletions are delayed by a configurable grace period (default: 5 minutes)
- This prevents accidental data loss from rapid delete propagation

## File Watching

- Use `fsnotify` for real-time local change detection
- Debounce rapid changes (e.g., Obsidian saves frequently) — 2-second window
- Fall back to periodic full scan every 60 seconds (catches missed events)

## CLI Interface

```
s3-folder-sync init          # interactive setup, writes .s3sync/config.toml
s3-folder-sync start         # start daemon (foreground)
s3-folder-sync start -d      # start daemon (background, via launchd plist)
s3-folder-sync status        # show sync status, last sync time, pending changes
s3-folder-sync conflicts     # list unresolved conflict files
s3-folder-sync sync          # force immediate sync cycle
s3-folder-sync stop          # stop background daemon
```

## Config (`.s3sync/config.toml`)

```toml
[storage]
endpoint = "https://s3.amazonaws.com"   # or minio, r2, etc.
bucket = "my-sync-bucket"
prefix = "workspace/"                    # optional key prefix
region = "us-east-1"

[sync]
interval = 10                            # seconds between sync cycles
debounce = 2                             # seconds to wait after file change
delete_grace_period = 300                # seconds before propagating deletes

[machine]
id = "mac-1"                             # unique per machine

[ignore]
patterns = [
  ".DS_Store",
  "*.tmp",
  ".git/**",
  "node_modules/**",
  ".s3sync/**",
]
```

## Project Structure

```
s3-folder-sync/
├── cmd/
│   └── s3-folder-sync/
│       └── main.go
├── internal/
│   ├── config/       # config loading/validation
│   ├── state/        # SQLite state tracking
│   ├── sync/         # core sync engine
│   ├── watcher/      # fsnotify + debounce
│   ├── s3client/     # S3 operations (upload/download/list/delete)
│   └── conflict/     # conflict detection and resolution
├── .s3sync/          # runtime directory (per-watched-folder)
├── go.mod
├── go.sum
├── plan.md
└── README.md
```

## Dependencies

- `github.com/aws/aws-sdk-go-v2` — S3 client
- `github.com/fsnotify/fsnotify` — file watching
- `github.com/mattn/go-sqlite3` — local state (CGo) OR `modernc.org/sqlite` (pure Go)
- `github.com/BurntSushi/toml` — config parsing
- `github.com/spf13/cobra` — CLI framework

## Implementation Order

1. **Project scaffold** — go module, CLI skeleton with cobra
2. **Config** — load/save `.s3sync/config.toml`, `init` command
3. **S3 client** — wrapper around aws-sdk-go-v2 for upload/download/list/delete with metadata
4. **State DB** — SQLite schema, CRUD operations for file state
5. **Sync engine** — core algorithm: scan local, list remote, diff, resolve, execute
6. **File watcher** — fsnotify integration with debounce
7. **Daemon mode** — background process, `start`/`stop`/`status` commands
8. **Conflict handling** — conflict file creation, `conflicts` command
9. **Ignore patterns** — glob matching for skip rules
10. **Polish** — logging, error handling, graceful shutdown, launchd plist generation

## Edge Cases to Handle

- Large files (stream upload/download, don't buffer in memory)
- Binary files (Obsidian plugins, images) — sync as-is, no special handling needed
- Rapid successive saves — debounce prevents thrashing
- Network failures — retry with backoff, don't corrupt state
- Partial uploads — use multipart upload, verify ETag after
- Clock skew between machines — use content hashes as primary, timestamps as tiebreaker
- First sync on new machine — pull everything from S3, don't treat existing remote as "new"
