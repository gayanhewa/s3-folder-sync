"""Tests for config module."""

from pathlib import Path

import pytest

from s3_folder_sync.config import Config, StorageConfig, SyncConfig


class TestConfig:
    def test_create_and_load(self, tmp_watch_dir):
        config = Config.create(
            watch_path=str(tmp_watch_dir),
            endpoint="https://s3.example.com",
            bucket="my-bucket",
            prefix="data/",
            region="eu-west-1",
            machine_id="mac-1",
        )

        assert config.config_file.exists()
        assert config.storage.bucket == "my-bucket"
        assert config.machine.id == "mac-1"

        loaded = Config.load(tmp_watch_dir)
        assert loaded.storage.endpoint == "https://s3.example.com"
        assert loaded.storage.bucket == "my-bucket"
        assert loaded.storage.prefix == "data/"
        assert loaded.storage.region == "eu-west-1"
        assert loaded.machine.id == "mac-1"

    def test_load_nonexistent_raises(self, tmp_watch_dir):
        with pytest.raises(FileNotFoundError, match="No config found"):
            Config.load(tmp_watch_dir)

    def test_sync_dir_paths(self, tmp_watch_dir):
        config = Config(watch_path=str(tmp_watch_dir))
        assert config.sync_dir == tmp_watch_dir / ".s3sync"
        assert config.trash_dir == tmp_watch_dir / ".s3sync" / "trash"
        assert config.db_path == tmp_watch_dir / ".s3sync" / "state.db"
        assert config.pid_file == tmp_watch_dir / ".s3sync" / "daemon.pid"

    def test_ensure_dirs(self, tmp_watch_dir):
        config = Config(watch_path=str(tmp_watch_dir))
        config.ensure_dirs()
        assert config.sync_dir.is_dir()
        assert config.trash_dir.is_dir()

    def test_default_ignore_patterns(self, tmp_watch_dir):
        config = Config(watch_path=str(tmp_watch_dir))
        assert ".DS_Store" in config.ignore_patterns
        assert ".s3sync/**" in config.ignore_patterns

    def test_save_preserves_custom_ignore(self, tmp_watch_dir):
        config = Config.create(
            watch_path=str(tmp_watch_dir),
            endpoint="https://s3.example.com",
            bucket="test",
        )
        config.ignore_patterns.append("*.log")
        config.save()

        loaded = Config.load(tmp_watch_dir)
        assert "*.log" in loaded.ignore_patterns

    def test_to_dict(self, config):
        d = config.to_dict()
        assert d["storage"]["bucket"] == "test-sync-bucket"
        assert d["machine"]["id"] == "test-machine"
        assert isinstance(d["ignore"]["patterns"], list)
