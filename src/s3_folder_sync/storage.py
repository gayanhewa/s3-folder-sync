"""Storage client factory."""

from __future__ import annotations

from s3_folder_sync.config import Config


def create_storage_client(config: Config):
    """Create the appropriate storage client based on config backend."""
    if config.storage.backend == "bunny":
        from s3_folder_sync.bunny_client import BunnyClient
        return BunnyClient(config)
    else:
        from s3_folder_sync.s3client import S3Client
        return S3Client(config)
