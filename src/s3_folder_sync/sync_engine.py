"""Core sync engine: scan, diff, resolve conflicts, execute."""

from __future__ import annotations

import hashlib
import logging
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from s3_folder_sync.config import Config
from s3_folder_sync.ignore import IgnoreMatcher
from s3_folder_sync.s3client import S3Client
from s3_folder_sync.state import FileState, StateDB

logger = logging.getLogger(__name__)


class Action(Enum):
    PUSH = "push"
    PULL = "pull"
    CONFLICT = "conflict"
    DELETE_REMOTE = "delete_remote"
    DELETE_LOCAL = "delete_local"
    NOOP = "noop"


@dataclass
class SyncAction:
    action: Action
    relative_path: str
    reason: str = ""


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SyncEngine:
    def __init__(self, config: Config, s3: S3Client, db: StateDB) -> None:
        self.config = config
        self.s3 = s3
        self.db = db
        self.watch_path = Path(config.watch_path)
        self.ignore = IgnoreMatcher(config.ignore_patterns)

    def scan_local(self) -> dict[str, tuple[str, float]]:
        """Scan local directory, return {relative_path: (hash, mtime)}."""
        result = {}
        for path in self.watch_path.rglob("*"):
            if not path.is_file():
                continue
            try:
                relative = str(path.relative_to(self.watch_path))
            except ValueError:
                continue

            if self.ignore.is_ignored(relative):
                continue

            try:
                content_hash = hash_file(path)
                mtime = path.stat().st_mtime
                result[relative] = (content_hash, mtime)
            except (OSError, PermissionError) as e:
                logger.warning("Cannot read %s: %s", path, e)

        return result

    def scan_remote(self) -> dict[str, dict]:
        """List S3 objects, return {relative_path: metadata}."""
        objects = self.s3.list_objects()
        result = {}
        for obj in objects:
            rp = obj["relative_path"]
            if self.ignore.is_ignored(rp):
                continue
            result[rp] = obj
        return result

    def compute_actions(
        self,
        local_files: dict[str, tuple[str, float]],
        remote_files: dict[str, dict],
    ) -> list[SyncAction]:
        """Determine what sync actions to take."""
        actions = []
        all_paths = set(local_files.keys()) | set(remote_files.keys())
        known_states = {s.relative_path: s for s in self.db.get_all()}

        for path in sorted(all_paths):
            local = local_files.get(path)
            remote = remote_files.get(path)
            known = known_states.get(path)

            action = self._resolve(path, local, remote, known)
            if action.action != Action.NOOP:
                actions.append(action)

        # Handle pending deletes whose grace period has expired
        now = now_iso()
        for path in self.db.get_pending_deletes(before=now):
            if path not in local_files and path not in remote_files:
                self.db.remove_pending_delete(path)
                continue
            if path not in local_files and path in remote_files:
                actions.append(SyncAction(
                    action=Action.DELETE_REMOTE,
                    relative_path=path,
                    reason="delete grace period expired",
                ))

        return actions

    def _resolve(
        self,
        path: str,
        local: tuple[str, float] | None,
        remote: dict | None,
        known: FileState | None,
    ) -> SyncAction:
        local_hash = local[0] if local else None
        remote_etag = remote["etag"] if remote else None

        # New local file, not on remote
        if local and not remote and not known:
            return SyncAction(Action.PUSH, path, "new local file")

        # New remote file, not local
        if remote and not local and not known:
            return SyncAction(Action.PULL, path, "new remote file")

        # Both exist
        if local and remote and known:
            local_changed = local_hash != known.content_hash
            remote_changed = remote_etag != known.last_synced_etag

            if not local_changed and not remote_changed:
                return SyncAction(Action.NOOP, path)

            if local_changed and not remote_changed:
                return SyncAction(Action.PUSH, path, "local modification")

            if remote_changed and not local_changed:
                return SyncAction(Action.PULL, path, "remote modification")

            # Both changed
            if local_hash == self._get_remote_hash(path):
                return SyncAction(Action.NOOP, path, "same content")
            return SyncAction(Action.CONFLICT, path, "both modified")

        # Both exist but no known state (first sync with existing remote)
        if local and remote and not known:
            if local_hash == self._get_remote_hash(path):
                return SyncAction(Action.NOOP, path, "same content")
            return SyncAction(Action.CONFLICT, path, "both exist, no prior state")

        # Local deleted (was known, now gone locally, still on remote)
        if not local and remote and known and not known.is_deleted:
            return SyncAction(Action.DELETE_REMOTE, path, "deleted locally")

        # Remote deleted (was known, now gone remotely, still local)
        if local and not remote and known:
            return SyncAction(Action.DELETE_LOCAL, path, "deleted remotely")

        return SyncAction(Action.NOOP, path)

    def _get_remote_hash(self, path: str) -> str | None:
        metadata = self.s3.get_metadata(path)
        return metadata.get("source-hash")

    def execute(self, actions: list[SyncAction]) -> list[str]:
        """Execute sync actions. Returns list of conflict file paths."""
        conflicts = []

        for action in actions:
            try:
                if action.action == Action.PUSH:
                    self._do_push(action.relative_path)
                elif action.action == Action.PULL:
                    self._do_pull(action.relative_path)
                elif action.action == Action.CONFLICT:
                    conflict_path = self._do_conflict(action.relative_path)
                    conflicts.append(conflict_path)
                elif action.action == Action.DELETE_REMOTE:
                    self._do_delete_remote(action.relative_path)
                elif action.action == Action.DELETE_LOCAL:
                    self._do_delete_local(action.relative_path)
            except Exception:
                logger.exception("Failed to execute %s for %s", action.action, action.relative_path)

        return conflicts

    def _do_push(self, relative_path: str) -> None:
        local_path = self.watch_path / relative_path
        content_hash = hash_file(local_path)
        ts = now_iso()

        etag = self.s3.upload(
            local_path=local_path,
            relative_path=relative_path,
            content_hash=content_hash,
            machine_id=self.config.machine.id,
            synced_at=ts,
        )

        self.db.upsert(FileState(
            relative_path=relative_path,
            content_hash=content_hash,
            local_mtime=local_path.stat().st_mtime,
            last_synced_etag=etag,
            last_synced_at=ts,
        ))
        logger.info("Pushed: %s", relative_path)

    def _do_pull(self, relative_path: str) -> None:
        local_path = self.watch_path / relative_path
        metadata = self.s3.download(relative_path, local_path)

        content_hash = hash_file(local_path)
        info = self.s3.head(relative_path)
        etag = info["etag"] if info else ""
        ts = now_iso()

        self.db.upsert(FileState(
            relative_path=relative_path,
            content_hash=content_hash,
            local_mtime=local_path.stat().st_mtime,
            last_synced_etag=etag,
            last_synced_at=ts,
        ))
        logger.info("Pulled: %s", relative_path)

    def _do_conflict(self, relative_path: str) -> str:
        local_path = self.watch_path / relative_path
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        machine_id = self.config.machine.id

        stem = local_path.stem
        suffix = local_path.suffix
        conflict_name = f"{stem}.conflict.{machine_id}.{ts}{suffix}"
        conflict_path = local_path.parent / conflict_name

        # Save local version as conflict file
        shutil.copy2(local_path, conflict_path)

        # Pull remote version as canonical
        self._do_pull(relative_path)

        # Push the conflict file so the other machine sees it too
        self._do_push(str(conflict_path.relative_to(self.watch_path)))

        logger.warning("Conflict: %s -> saved local as %s", relative_path, conflict_name)
        return str(conflict_path.relative_to(self.watch_path))

    def _do_delete_remote(self, relative_path: str) -> None:
        # Use grace period
        now = now_iso()
        from datetime import timedelta
        grace = timedelta(seconds=self.config.sync.delete_grace_period)
        propagate_after = (datetime.now(timezone.utc) + grace).isoformat()

        self.db.add_pending_delete(relative_path, now, propagate_after)

        # Mark as deleted in state
        state = self.db.get(relative_path)
        if state:
            state.is_deleted = True
            self.db.upsert(state)

        logger.info(
            "Scheduled remote delete: %s (propagates after %ds)",
            relative_path,
            self.config.sync.delete_grace_period,
        )

    def _do_delete_local(self, relative_path: str) -> None:
        local_path = self.watch_path / relative_path
        if local_path.exists():
            # Move to trash
            trash_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            trash_dir = self.config.trash_dir / trash_date
            trash_dir.mkdir(parents=True, exist_ok=True)
            trash_path = trash_dir / local_path.name
            shutil.move(str(local_path), str(trash_path))
            logger.info("Moved to trash: %s -> %s", relative_path, trash_path)

        self.db.delete(relative_path)

    def run_cycle(self) -> list[str]:
        """Run a single sync cycle. Returns list of conflict paths."""
        logger.debug("Starting sync cycle")
        local_files = self.scan_local()
        remote_files = self.scan_remote()
        actions = self.compute_actions(local_files, remote_files)

        if actions:
            logger.info("Sync actions: %d", len(actions))
            for a in actions:
                logger.debug("  %s %s (%s)", a.action.value, a.relative_path, a.reason)

        conflicts = self.execute(actions)

        # Process expired pending deletes
        now = now_iso()
        for path in self.db.get_pending_deletes(before=now):
            try:
                self.s3.delete(path)
                self.db.remove_pending_delete(path)
                self.db.delete(path)
                logger.info("Propagated remote delete: %s", path)
            except Exception:
                logger.exception("Failed to propagate delete: %s", path)

        logger.debug("Sync cycle complete")
        return conflicts
