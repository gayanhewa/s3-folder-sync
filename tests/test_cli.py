"""Tests for CLI interface."""

import pytest
from click.testing import CliRunner

from s3_folder_sync.cli import main
from s3_folder_sync.config import Config


class TestCLI:
    def test_init_command(self, tmp_watch_dir):
        runner = CliRunner()
        result = runner.invoke(main, [
            "init",
            "--path", str(tmp_watch_dir),
            "--endpoint", "https://s3.example.com",
            "--bucket", "test-bucket",
            "--prefix", "data/",
            "--machine-id", "test-mac",
        ])
        assert result.exit_code == 0
        assert "Initialized" in result.output
        assert "test-mac" in result.output

        # Config file should exist
        config = Config.load(tmp_watch_dir)
        assert config.storage.bucket == "test-bucket"

    def test_init_nonexistent_dir(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(main, [
            "init",
            "--path", str(tmp_path / "nonexistent"),
            "--endpoint", "https://s3.example.com",
            "--bucket", "test",
        ])
        assert result.exit_code == 1

    def test_status_no_config(self, tmp_watch_dir):
        runner = CliRunner()
        result = runner.invoke(main, ["status", "--path", str(tmp_watch_dir)])
        assert result.exit_code == 1

    def test_status_with_config(self, tmp_watch_dir):
        Config.create(
            watch_path=str(tmp_watch_dir),
            endpoint="https://s3.example.com",
            bucket="test",
            machine_id="my-mac",
        )
        runner = CliRunner()
        result = runner.invoke(main, ["status", "--path", str(tmp_watch_dir)])
        assert result.exit_code == 0
        assert "my-mac" in result.output
        assert "stopped" in result.output

    def test_conflicts_empty(self, tmp_watch_dir):
        runner = CliRunner()
        result = runner.invoke(main, ["conflicts", "--path", str(tmp_watch_dir)])
        assert result.exit_code == 0
        assert "No conflict files" in result.output

    def test_conflicts_found(self, tmp_watch_dir):
        (tmp_watch_dir / "doc.conflict.mac-1.20240101.md").write_text("conflict")
        runner = CliRunner()
        result = runner.invoke(main, ["conflicts", "--path", str(tmp_watch_dir)])
        assert result.exit_code == 0
        assert "doc.conflict" in result.output

    def test_stop_no_daemon(self, tmp_watch_dir):
        Config.create(
            watch_path=str(tmp_watch_dir),
            endpoint="https://s3.example.com",
            bucket="test",
        )
        runner = CliRunner()
        result = runner.invoke(main, ["stop", "--path", str(tmp_watch_dir)])
        assert result.exit_code == 0
        assert "No running daemon" in result.output

    def test_verbose_flag(self, tmp_watch_dir):
        runner = CliRunner()
        result = runner.invoke(main, ["-v", "conflicts", "--path", str(tmp_watch_dir)])
        assert result.exit_code == 0
