"""Configuration loading and management."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field
from pathlib import Path

import toml

SYNC_DIR_NAME = ".s3sync"
CONFIG_FILE_NAME = "config.toml"
TRASH_DIR_NAME = "trash"

DEFAULT_IGNORE_PATTERNS = [
    ".DS_Store",
    "*.tmp",
    ".git/**",
    "node_modules/**",
    ".s3sync/**",
    "__pycache__/**",
    "*.pyc",
]


@dataclass
class StorageConfig:
    endpoint: str = ""
    bucket: str = ""
    prefix: str = ""
    region: str = "us-east-1"
    access_key: str = ""
    secret_key: str = ""
    backend: str = "s3"  # "s3" or "bunny"


@dataclass
class SyncConfig:
    interval: int = 10
    debounce: float = 2.0
    delete_grace_period: int = 300


@dataclass
class MachineConfig:
    id: str = field(default_factory=lambda: socket.gethostname())


@dataclass
class Config:
    storage: StorageConfig = field(default_factory=StorageConfig)
    sync: SyncConfig = field(default_factory=SyncConfig)
    machine: MachineConfig = field(default_factory=MachineConfig)
    ignore_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_IGNORE_PATTERNS))
    watch_path: str = ""

    @property
    def sync_dir(self) -> Path:
        return Path(self.watch_path) / SYNC_DIR_NAME

    @property
    def trash_dir(self) -> Path:
        return self.sync_dir / TRASH_DIR_NAME

    @property
    def config_file(self) -> Path:
        return self.sync_dir / CONFIG_FILE_NAME

    @property
    def db_path(self) -> Path:
        return self.sync_dir / "state.db"

    @property
    def pid_file(self) -> Path:
        return self.sync_dir / "daemon.pid"

    def ensure_dirs(self) -> None:
        self.sync_dir.mkdir(parents=True, exist_ok=True)
        self.trash_dir.mkdir(parents=True, exist_ok=True)

    def to_dict(self) -> dict:
        return {
            "storage": {
                "endpoint": self.storage.endpoint,
                "bucket": self.storage.bucket,
                "prefix": self.storage.prefix,
                "region": self.storage.region,
                "access_key": self.storage.access_key,
                "secret_key": self.storage.secret_key,
                "backend": self.storage.backend,
            },
            "sync": {
                "interval": self.sync.interval,
                "debounce": self.sync.debounce,
                "delete_grace_period": self.sync.delete_grace_period,
            },
            "machine": {
                "id": self.machine.id,
            },
            "ignore": {
                "patterns": self.ignore_patterns,
            },
        }

    def save(self) -> None:
        self.ensure_dirs()
        with open(self.config_file, "w") as f:
            toml.dump(self.to_dict(), f)

    @classmethod
    def load(cls, watch_path: str | Path) -> Config:
        watch_path = str(Path(watch_path).resolve())
        config_file = Path(watch_path) / SYNC_DIR_NAME / CONFIG_FILE_NAME

        if not config_file.exists():
            raise FileNotFoundError(
                f"No config found at {config_file}. Run 's3-folder-sync init' first."
            )

        data = toml.load(config_file)

        storage_data = data.get("storage", {})
        sync_data = data.get("sync", {})
        machine_data = data.get("machine", {})
        ignore_data = data.get("ignore", {})

        storage_fields = {
            k: v for k, v in storage_data.items()
            if k in StorageConfig.__dataclass_fields__
        }
        return cls(
            storage=StorageConfig(**storage_fields),
            sync=SyncConfig(**{
                k: v for k, v in sync_data.items()
                if k in ("interval", "debounce", "delete_grace_period")
            }),
            machine=MachineConfig(**machine_data),
            ignore_patterns=ignore_data.get("patterns", list(DEFAULT_IGNORE_PATTERNS)),
            watch_path=watch_path,
        )

    @classmethod
    def create(
        cls,
        watch_path: str | Path,
        endpoint: str,
        bucket: str,
        prefix: str = "",
        region: str = "us-east-1",
        access_key: str = "",
        secret_key: str = "",
        machine_id: str | None = None,
        backend: str = "s3",
    ) -> Config:
        watch_path = str(Path(watch_path).resolve())
        config = cls(
            storage=StorageConfig(
                endpoint=endpoint,
                bucket=bucket,
                prefix=prefix,
                region=region,
                access_key=access_key,
                secret_key=secret_key,
                backend=backend,
            ),
            machine=MachineConfig(id=machine_id or socket.gethostname()),
            watch_path=watch_path,
        )
        config.save()
        return config
