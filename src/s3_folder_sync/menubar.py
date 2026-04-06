"""macOS menu bar app for s3-folder-sync."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

import rumps

from s3_folder_sync.config import Config
from s3_folder_sync.ignore import IgnoreMatcher
from s3_folder_sync.state import StateDB
from s3_folder_sync.storage import create_storage_client
from s3_folder_sync.sync_engine import SyncEngine

logger = logging.getLogger(__name__)

# Menu item keys (internal identifiers, never change)
KEY_STATUS = "Status: Idle"
KEY_LAST_SYNC = "Last sync: never"
KEY_FILES = "Files: 0 tracked"
KEY_CONFLICTS = "Conflicts: none"
KEY_SYNC_NOW = "Sync Now"
KEY_START_STOP = "Start Sync"
KEY_OPEN_FOLDER = "Open Folder"
KEY_VIEW_CONFLICTS = "View Conflicts..."
KEY_QUIT = "Quit"


class SyncMenuBarApp(rumps.App):
    def __init__(self, watch_path: str) -> None:
        super().__init__("S3 Sync", title="⇅")

        self.watch_path = Path(watch_path).resolve()
        self.config: Config | None = None
        self.engine: SyncEngine | None = None
        self.db: StateDB | None = None
        self._syncing = False
        self._running = False
        self._sync_thread: threading.Thread | None = None
        self._last_sync: str = "never"
        self._conflict_count = 0
        self._file_count = 0

        self.menu = [
            rumps.MenuItem(KEY_STATUS),
            rumps.MenuItem(KEY_LAST_SYNC),
            rumps.MenuItem(KEY_FILES),
            rumps.MenuItem(KEY_CONFLICTS),
            None,  # separator
            rumps.MenuItem(KEY_SYNC_NOW, callback=self._on_sync_now),
            rumps.MenuItem(KEY_START_STOP, callback=self._on_start_stop),
            None,
            rumps.MenuItem(KEY_OPEN_FOLDER, callback=self._on_open_folder),
            rumps.MenuItem(KEY_VIEW_CONFLICTS, callback=self._on_view_conflicts),
        ]

        # Disable info items (not clickable)
        self.menu[KEY_STATUS].set_callback(None)
        self.menu[KEY_LAST_SYNC].set_callback(None)
        self.menu[KEY_FILES].set_callback(None)
        self.menu[KEY_CONFLICTS].set_callback(None)

        self._load_config()

    def _load_config(self) -> None:
        try:
            self.config = Config.load(self.watch_path)
            self.db = StateDB(self.config.db_path)
            s3 = create_storage_client(self.config)
            self.engine = SyncEngine(self.config, s3, self.db)
            self._update_status("Ready")
        except FileNotFoundError:
            self._update_status("Not configured")
            rumps.notification(
                "s3-folder-sync",
                "Not Configured",
                f"Run 's3-folder-sync init --path {self.watch_path}' first.",
            )

    def _update_status(self, status: str) -> None:
        self.menu[KEY_STATUS].title = f"Status: {status}"

    def _update_menu(self) -> None:
        self.menu[KEY_LAST_SYNC].title = f"Last sync: {self._last_sync}"
        self.menu[KEY_FILES].title = f"Files: {self._file_count} tracked"

        if self._conflict_count > 0:
            self.menu[KEY_CONFLICTS].title = f"Conflicts: {self._conflict_count}"
        else:
            self.menu[KEY_CONFLICTS].title = "Conflicts: none"

        if self._running:
            self.menu[KEY_START_STOP].title = "Stop Sync"
            self.title = "⇅"
        else:
            self.menu[KEY_START_STOP].title = "Start Sync"
            self.title = "⇅"

    def _on_sync_now(self, sender: rumps.MenuItem) -> None:
        if not self.engine:
            rumps.notification("s3-folder-sync", "Error", "Not configured")
            return
        if self._syncing:
            return

        thread = threading.Thread(target=self._do_sync, daemon=True)
        thread.start()

    def _on_start_stop(self, sender: rumps.MenuItem) -> None:
        if not self.engine:
            rumps.notification("s3-folder-sync", "Error", "Not configured")
            return

        if self._running:
            self._running = False
            self._update_status("Stopped")
            self._update_menu()
        else:
            self._running = True
            self._update_status("Running")
            self._update_menu()
            self._start_sync_loop()

    def _on_open_folder(self, sender: rumps.MenuItem) -> None:
        import subprocess
        subprocess.Popen(["open", str(self.watch_path)])

    def _on_view_conflicts(self, sender: rumps.MenuItem) -> None:
        conflict_files = list(self.watch_path.rglob("*.conflict.*"))
        if not conflict_files:
            rumps.notification("s3-folder-sync", "No Conflicts", "All clear!")
            return

        msg = "\n".join(
            str(f.relative_to(self.watch_path)) for f in sorted(conflict_files)[:20]
        )
        if len(conflict_files) > 20:
            msg += f"\n... and {len(conflict_files) - 20} more"

        rumps.alert(
            title="Conflict Files",
            message=msg,
        )

    def _start_sync_loop(self) -> None:
        def loop():
            import time
            while self._running:
                self._do_sync()
                interval = self.config.sync.interval if self.config else 10
                for _ in range(interval):
                    if not self._running:
                        break
                    time.sleep(1)

        self._sync_thread = threading.Thread(target=loop, daemon=True)
        self._sync_thread.start()

    def _do_sync(self) -> None:
        if self._syncing or not self.engine:
            return

        self._syncing = True
        self._update_status("Syncing...")
        self.title = "⇅↻"

        try:
            conflicts = self.engine.run_cycle()

            self._last_sync = datetime.now(timezone.utc).strftime("%H:%M:%S")
            self._file_count = len(self.db.get_all()) if self.db else 0
            self._conflict_count = len(list(self.watch_path.rglob("*.conflict.*")))

            if conflicts:
                rumps.notification(
                    "s3-folder-sync",
                    "Conflicts Detected",
                    f"{len(conflicts)} file(s) had conflicts",
                )

            status = "Running" if self._running else "Ready"
            self._update_status(status)
        except Exception as e:
            logger.exception("Sync failed")
            self._update_status(f"Error: {e}")
            rumps.notification("s3-folder-sync", "Sync Error", str(e))
        finally:
            self._syncing = False
            self.title = "⇅"
            self._update_menu()

    @rumps.timer(60)
    def _periodic_refresh(self, sender: rumps.Timer) -> None:
        """Refresh conflict count and file count periodically."""
        if self.db:
            self._file_count = len(self.db.get_all())
            self._conflict_count = len(list(self.watch_path.rglob("*.conflict.*")))
            self._update_menu()


def run_menubar(watch_path: str) -> None:
    """Entry point for the menu bar app."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    app = SyncMenuBarApp(watch_path)
    app.run()
