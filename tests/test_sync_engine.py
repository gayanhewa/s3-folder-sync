"""Tests for sync engine."""

from pathlib import Path

from s3_folder_sync.s3client import S3Client
from s3_folder_sync.state import FileState, StateDB
from s3_folder_sync.sync_engine import Action, SyncEngine, hash_file


class TestHashFile:
    def test_hash_consistency(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello")
        h1 = hash_file(f)
        h2 = hash_file(f)
        assert h1 == h2

    def test_different_content_different_hash(self, tmp_path):
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("hello")
        f2.write_text("world")
        assert hash_file(f1) != hash_file(f2)


class TestSyncEngine:
    def _make_engine(self, config, mock_s3):
        s3 = S3Client(config)
        db = StateDB(config.db_path)
        return SyncEngine(config, s3, db), s3, db

    def test_new_local_file_pushes(self, config, mock_s3, tmp_watch_dir):
        engine, s3, db = self._make_engine(config, mock_s3)

        (tmp_watch_dir / "new.md").write_text("new content")

        local = engine.scan_local()
        remote = engine.scan_remote()
        actions = engine.compute_actions(local, remote)

        assert len(actions) == 1
        assert actions[0].action == Action.PUSH
        assert actions[0].relative_path == "new.md"
        db.close()

    def test_new_remote_file_pulls(self, config, mock_s3, tmp_watch_dir):
        engine, s3, db = self._make_engine(config, mock_s3)

        # Upload a file directly to S3
        remote_file = tmp_watch_dir / "_remote_tmp.md"
        remote_file.write_text("remote content")
        s3.upload(remote_file, "remote.md", "hash1", "other-machine", "ts1")
        remote_file.unlink()

        local = engine.scan_local()
        remote = engine.scan_remote()
        actions = engine.compute_actions(local, remote)

        assert len(actions) == 1
        assert actions[0].action == Action.PULL
        assert actions[0].relative_path == "remote.md"
        db.close()

    def test_unchanged_file_noop(self, config, mock_s3, tmp_watch_dir):
        engine, s3, db = self._make_engine(config, mock_s3)

        f = tmp_watch_dir / "stable.md"
        f.write_text("stable")
        content_hash = hash_file(f)

        # Push and record state
        etag = s3.upload(f, "stable.md", content_hash, "test-machine", "ts1")
        db.upsert(FileState("stable.md", content_hash, f.stat().st_mtime, etag, "ts1"))

        local = engine.scan_local()
        remote = engine.scan_remote()
        actions = engine.compute_actions(local, remote)

        assert len(actions) == 0
        db.close()

    def test_local_modification_pushes(self, config, mock_s3, tmp_watch_dir):
        engine, s3, db = self._make_engine(config, mock_s3)

        f = tmp_watch_dir / "doc.md"
        f.write_text("v1")
        h1 = hash_file(f)
        etag = s3.upload(f, "doc.md", h1, "test-machine", "ts1")
        db.upsert(FileState("doc.md", h1, f.stat().st_mtime, etag, "ts1"))

        # Modify locally
        f.write_text("v2")

        local = engine.scan_local()
        remote = engine.scan_remote()
        actions = engine.compute_actions(local, remote)

        assert len(actions) == 1
        assert actions[0].action == Action.PUSH
        db.close()

    def test_conflict_detection(self, config, mock_s3, tmp_watch_dir):
        engine, s3, db = self._make_engine(config, mock_s3)

        f = tmp_watch_dir / "shared.md"
        f.write_text("original")
        h1 = hash_file(f)
        etag = s3.upload(f, "shared.md", h1, "test-machine", "ts1")
        db.upsert(FileState("shared.md", h1, f.stat().st_mtime, etag, "ts1"))

        # Modify locally
        f.write_text("local change")

        # Modify remotely (simulate other machine)
        remote_tmp = tmp_watch_dir / "_rtmp.md"
        remote_tmp.write_text("remote change")
        s3.upload(remote_tmp, "shared.md", hash_file(remote_tmp), "other-machine", "ts2")
        remote_tmp.unlink()

        local = engine.scan_local()
        remote = engine.scan_remote()
        actions = engine.compute_actions(local, remote)

        assert len(actions) == 1
        assert actions[0].action == Action.CONFLICT
        db.close()

    def test_full_sync_cycle(self, config, mock_s3, tmp_watch_dir):
        engine, s3, db = self._make_engine(config, mock_s3)

        (tmp_watch_dir / "file1.md").write_text("content 1")
        (tmp_watch_dir / "file2.md").write_text("content 2")

        conflicts = engine.run_cycle()
        assert conflicts == []

        # Verify files are on S3
        objects = s3.list_objects()
        paths = {o["relative_path"] for o in objects}
        assert "file1.md" in paths
        assert "file2.md" in paths

        # Verify state is tracked
        assert db.get("file1.md") is not None
        assert db.get("file2.md") is not None
        db.close()

    def test_conflict_creates_conflict_file(self, config, mock_s3, tmp_watch_dir):
        engine, s3, db = self._make_engine(config, mock_s3)

        f = tmp_watch_dir / "doc.md"
        f.write_text("original")
        h = hash_file(f)
        etag = s3.upload(f, "doc.md", h, "test-machine", "ts1")
        db.upsert(FileState("doc.md", h, f.stat().st_mtime, etag, "ts1"))

        # Local change
        f.write_text("local version")

        # Remote change
        rtmp = tmp_watch_dir / "_r.md"
        rtmp.write_text("remote version")
        s3.upload(rtmp, "doc.md", hash_file(rtmp), "other", "ts2")
        rtmp.unlink()

        conflicts = engine.run_cycle()
        assert len(conflicts) == 1

        # Canonical file should have remote content
        assert f.read_text() == "remote version"

        # Conflict file should exist with local content
        conflict_files = list(tmp_watch_dir.glob("doc.conflict.*"))
        assert len(conflict_files) == 1
        assert conflict_files[0].read_text() == "local version"
        db.close()

    def test_local_delete_schedules_remote_delete(self, config, mock_s3, tmp_watch_dir):
        engine, s3, db = self._make_engine(config, mock_s3)

        f = tmp_watch_dir / "todelete.md"
        f.write_text("will be deleted")

        # Sync it first
        engine.run_cycle()

        # Delete locally
        f.unlink()

        local = engine.scan_local()
        remote = engine.scan_remote()
        actions = engine.compute_actions(local, remote)

        assert any(a.action == Action.DELETE_REMOTE for a in actions)
        db.close()

    def test_remote_delete_moves_to_trash(self, config, mock_s3, tmp_watch_dir):
        engine, s3, db = self._make_engine(config, mock_s3)

        f = tmp_watch_dir / "file.md"
        f.write_text("content")

        # Sync it
        engine.run_cycle()

        # Delete from S3
        s3.delete("file.md")

        local = engine.scan_local()
        remote = engine.scan_remote()
        actions = engine.compute_actions(local, remote)

        assert any(a.action == Action.DELETE_LOCAL for a in actions)

        engine.execute(actions)

        # File should be gone from watch dir
        assert not f.exists()

        # File should be in trash
        trash_files = list(config.trash_dir.rglob("file.md"))
        assert len(trash_files) == 1
        assert trash_files[0].read_text() == "content"
        db.close()

    def test_ignored_files_not_synced(self, config, mock_s3, tmp_watch_dir):
        engine, s3, db = self._make_engine(config, mock_s3)

        (tmp_watch_dir / ".DS_Store").write_text("ds")
        (tmp_watch_dir / "real.md").write_text("real")

        local = engine.scan_local()
        assert ".DS_Store" not in local
        assert "real.md" in local
        db.close()
