"""Daemon process for continuous sync."""

from __future__ import annotations

import logging
import os
import signal
import sys
import time
from pathlib import Path

from s3_folder_sync.config import Config
from s3_folder_sync.ignore import IgnoreMatcher
from s3_folder_sync.state import StateDB
from s3_folder_sync.storage import create_storage_client
from s3_folder_sync.sync_engine import SyncEngine
from s3_folder_sync.watcher import FileWatcher

logger = logging.getLogger(__name__)


class SyncDaemon:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.s3 = create_storage_client(config)
        self.db = StateDB(config.db_path)
        self.engine = SyncEngine(config, self.s3, self.db)
        self.ignore = IgnoreMatcher(config.ignore_patterns)
        self.watcher = FileWatcher(
            Path(config.watch_path),
            self.ignore,
            config.sync.debounce,
        )
        self._running = False

    def start(self, foreground: bool = True) -> None:
        if not foreground:
            self._daemonize()

        self._write_pid()
        self._setup_signals()
        self._running = True

        logger.info(
            "Sync daemon started (machine=%s, interval=%ds, path=%s)",
            self.config.machine.id,
            self.config.sync.interval,
            self.config.watch_path,
        )

        self.watcher.start()
        last_full_scan = 0.0

        try:
            while self._running:
                changed = self.watcher.get_changed_files()
                now = time.time()

                # Run full sync cycle if there are changes or interval has passed
                if changed or (now - last_full_scan >= self.config.sync.interval):
                    try:
                        conflicts = self.engine.run_cycle()
                        if conflicts:
                            logger.warning(
                                "Conflicts detected: %s",
                                ", ".join(conflicts),
                            )
                    except Exception:
                        logger.exception("Sync cycle failed")
                    last_full_scan = now

                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Interrupted")
        finally:
            self.stop()

    def stop(self) -> None:
        self._running = False
        self.watcher.stop()
        self.db.close()
        self._remove_pid()
        logger.info("Sync daemon stopped")

    def _setup_signals(self) -> None:
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

    def _handle_signal(self, signum: int, frame) -> None:
        logger.info("Received signal %d", signum)
        self._running = False

    def _write_pid(self) -> None:
        self.config.pid_file.write_text(str(os.getpid()))

    def _remove_pid(self) -> None:
        if self.config.pid_file.exists():
            self.config.pid_file.unlink()

    def _daemonize(self) -> None:
        # Double fork to detach from terminal
        if os.fork() > 0:
            sys.exit(0)
        os.setsid()
        if os.fork() > 0:
            sys.exit(0)

        # Redirect stdio
        sys.stdin = open(os.devnull, "r")
        log_file = self.config.sync_dir / "daemon.log"
        sys.stdout = open(log_file, "a")
        sys.stderr = open(log_file, "a")

    @staticmethod
    def is_running(config: Config) -> tuple[bool, int | None]:
        if not config.pid_file.exists():
            return False, None
        try:
            pid = int(config.pid_file.read_text().strip())
            os.kill(pid, 0)  # Check if process exists
            return True, pid
        except (ValueError, ProcessLookupError, PermissionError):
            return False, None

    @staticmethod
    def stop_daemon(config: Config) -> bool:
        running, pid = SyncDaemon.is_running(config)
        if not running or pid is None:
            return False
        os.kill(pid, signal.SIGTERM)
        # Wait for process to exit
        for _ in range(10):
            try:
                os.kill(pid, 0)
                time.sleep(0.5)
            except ProcessLookupError:
                return True
        return False
