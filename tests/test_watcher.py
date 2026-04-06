"""Tests for file watcher with debounce."""

import time
from pathlib import Path

from s3_folder_sync.ignore import IgnoreMatcher
from s3_folder_sync.watcher import DebouncedHandler, FileWatcher


class TestDebouncedHandler:
    def test_debounce_settles(self, tmp_watch_dir):
        handler = DebouncedHandler(
            watch_path=tmp_watch_dir,
            ignore=IgnoreMatcher([".s3sync/**"]),
            debounce_seconds=0.1,
        )

        # Simulate event
        handler._pending["test.md"] = time.time() - 0.2  # Already settled
        settled = handler.get_changed_files()
        assert "test.md" in settled

    def test_debounce_pending(self, tmp_watch_dir):
        handler = DebouncedHandler(
            watch_path=tmp_watch_dir,
            ignore=IgnoreMatcher([]),
            debounce_seconds=5.0,
        )

        handler._pending["test.md"] = time.time()  # Just happened
        settled = handler.get_changed_files()
        assert len(settled) == 0
        assert handler.has_pending()

    def test_ignored_files_not_tracked(self, tmp_watch_dir):
        handler = DebouncedHandler(
            watch_path=tmp_watch_dir,
            ignore=IgnoreMatcher([".DS_Store"]),
            debounce_seconds=0.1,
        )

        from unittest.mock import MagicMock
        event = MagicMock()
        event.is_directory = False
        event.src_path = str(tmp_watch_dir / ".DS_Store")

        handler.on_any_event(event)
        assert not handler.has_pending()


class TestFileWatcher:
    def test_start_stop(self, tmp_watch_dir):
        watcher = FileWatcher(
            tmp_watch_dir,
            IgnoreMatcher([".s3sync/**"]),
            debounce_seconds=0.1,
        )
        watcher.start()
        time.sleep(0.1)
        watcher.stop()

    def test_detects_file_creation(self, tmp_watch_dir):
        watcher = FileWatcher(
            tmp_watch_dir,
            IgnoreMatcher([".s3sync/**"]),
            debounce_seconds=0.1,
        )
        watcher.start()

        try:
            (tmp_watch_dir / "new_file.md").write_text("hello")
            time.sleep(0.3)
            changed = watcher.get_changed_files()
            assert "new_file.md" in changed
        finally:
            watcher.stop()
