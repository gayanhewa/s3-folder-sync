"""Tests for state database."""

from s3_folder_sync.state import FileState, StateDB


class TestStateDB:
    def test_upsert_and_get(self, state_db):
        state = FileState(
            relative_path="notes/test.md",
            content_hash="abc123",
            local_mtime=1000.0,
            last_synced_etag="etag1",
            last_synced_at="2024-01-01T00:00:00",
        )
        state_db.upsert(state)

        result = state_db.get("notes/test.md")
        assert result is not None
        assert result.content_hash == "abc123"
        assert result.local_mtime == 1000.0
        assert result.last_synced_etag == "etag1"
        assert not result.is_deleted

    def test_get_nonexistent(self, state_db):
        assert state_db.get("nonexistent.txt") is None

    def test_upsert_updates_existing(self, state_db):
        state = FileState("test.md", "hash1", 1000.0, "etag1", "ts1")
        state_db.upsert(state)

        state.content_hash = "hash2"
        state.local_mtime = 2000.0
        state_db.upsert(state)

        result = state_db.get("test.md")
        assert result.content_hash == "hash2"
        assert result.local_mtime == 2000.0

    def test_get_all(self, state_db):
        state_db.upsert(FileState("a.md", "h1", 1.0, "e1", "t1"))
        state_db.upsert(FileState("b.md", "h2", 2.0, "e2", "t2"))
        state_db.upsert(FileState("c.md", "h3", 3.0, "e3", "t3"))

        all_states = state_db.get_all()
        assert len(all_states) == 3
        paths = {s.relative_path for s in all_states}
        assert paths == {"a.md", "b.md", "c.md"}

    def test_delete(self, state_db):
        state_db.upsert(FileState("test.md", "h1", 1.0, "e1", "t1"))
        state_db.delete("test.md")
        assert state_db.get("test.md") is None

    def test_is_deleted_flag(self, state_db):
        state = FileState("test.md", "h1", 1.0, "e1", "t1", is_deleted=True)
        state_db.upsert(state)

        result = state_db.get("test.md")
        assert result.is_deleted

    def test_pending_deletes(self, state_db):
        state_db.add_pending_delete("a.md", "2024-01-01T00:00:00", "2024-01-01T00:05:00")
        state_db.add_pending_delete("b.md", "2024-01-01T00:00:00", "2024-01-01T00:10:00")

        # Before either is ready
        result = state_db.get_pending_deletes("2024-01-01T00:04:00")
        assert len(result) == 0

        # After first is ready
        result = state_db.get_pending_deletes("2024-01-01T00:06:00")
        assert result == ["a.md"]

        # After both are ready
        result = state_db.get_pending_deletes("2024-01-01T00:11:00")
        assert len(result) == 2

    def test_remove_pending_delete(self, state_db):
        state_db.add_pending_delete("a.md", "2024-01-01T00:00:00", "2024-01-01T00:05:00")
        state_db.remove_pending_delete("a.md")

        result = state_db.get_pending_deletes("2025-01-01T00:00:00")
        assert len(result) == 0
