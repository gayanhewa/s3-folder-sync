"""File system watcher with debounce."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from s3_folder_sync.ignore import IgnoreMatcher

logger = logging.getLogger(__name__)


class DebouncedHandler(FileSystemEventHandler):
    """Collect file change events and debounce rapid changes."""

    def __init__(
        self,
        watch_path: Path,
        ignore: IgnoreMatcher,
        debounce_seconds: float = 2.0,
    ) -> None:
        self.watch_path = watch_path
        self.ignore = ignore
        self.debounce_seconds = debounce_seconds
        self._pending: dict[str, float] = {}
        self._lock = threading.Lock()

    def _relative(self, path: str) -> str | None:
        try:
            return str(Path(path).relative_to(self.watch_path))
        except ValueError:
            return None

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return

        relative = self._relative(event.src_path)
        if relative is None:
            return

        if self.ignore.is_ignored(relative):
            return

        with self._lock:
            self._pending[relative] = time.time()

    def get_changed_files(self) -> set[str]:
        """Return files that have settled (no changes within debounce window)."""
        now = time.time()
        settled = set()

        with self._lock:
            to_remove = []
            for path, last_change in self._pending.items():
                if now - last_change >= self.debounce_seconds:
                    settled.add(path)
                    to_remove.append(path)
            for path in to_remove:
                del self._pending[path]

        return settled

    def has_pending(self) -> bool:
        with self._lock:
            return len(self._pending) > 0


class FileWatcher:
    """Watch a directory for changes using watchdog."""

    def __init__(
        self,
        watch_path: Path,
        ignore: IgnoreMatcher,
        debounce_seconds: float = 2.0,
    ) -> None:
        self.handler = DebouncedHandler(watch_path, ignore, debounce_seconds)
        self._observer = Observer()
        self._observer.schedule(self.handler, str(watch_path), recursive=True)

    def start(self) -> None:
        self._observer.start()
        logger.info("File watcher started")

    def stop(self) -> None:
        self._observer.stop()
        self._observer.join()
        logger.info("File watcher stopped")

    def get_changed_files(self) -> set[str]:
        return self.handler.get_changed_files()

    def has_pending(self) -> bool:
        return self.handler.has_pending()
