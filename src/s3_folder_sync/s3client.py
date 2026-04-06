"""S3 client wrapper with metadata support."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

from s3_folder_sync.config import Config

logger = logging.getLogger(__name__)


class S3Client:
    """Wrapper around boto3 S3 client with sync metadata support."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.bucket = config.storage.bucket
        self.prefix = config.storage.prefix

        kwargs: dict[str, Any] = {
            "region_name": config.storage.region,
        }
        if config.storage.endpoint:
            kwargs["endpoint_url"] = config.storage.endpoint
        if config.storage.access_key:
            kwargs["aws_access_key_id"] = config.storage.access_key
            kwargs["aws_secret_access_key"] = config.storage.secret_key

        self._client = boto3.client("s3", **kwargs)

    def _s3_key(self, relative_path: str) -> str:
        if self.prefix:
            return f"{self.prefix.rstrip('/')}/{relative_path}"
        return relative_path

    def _relative_path(self, s3_key: str) -> str:
        if self.prefix:
            prefix = self.prefix.rstrip("/") + "/"
            if s3_key.startswith(prefix):
                return s3_key[len(prefix):]
        return s3_key

    def upload(
        self,
        local_path: Path,
        relative_path: str,
        content_hash: str,
        machine_id: str,
        synced_at: str,
    ) -> str:
        key = self._s3_key(relative_path)
        metadata = {
            "source-hash": content_hash,
            "machine-id": machine_id,
            "synced-at": synced_at,
        }

        with open(local_path, "rb") as f:
            resp = self._client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=f,
                Metadata=metadata,
            )

        etag = resp.get("ETag", "").strip('"')
        logger.debug("Uploaded %s -> s3://%s/%s (etag=%s)", relative_path, self.bucket, key, etag)
        return etag

    def download(self, relative_path: str, local_path: Path) -> dict[str, str]:
        key = self._s3_key(relative_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)

        resp = self._client.get_object(Bucket=self.bucket, Key=key)
        with open(local_path, "wb") as f:
            for chunk in resp["Body"].iter_chunks():
                f.write(chunk)

        metadata = resp.get("Metadata", {})
        logger.debug("Downloaded s3://%s/%s -> %s", self.bucket, key, relative_path)
        return metadata

    def head(self, relative_path: str) -> dict[str, Any] | None:
        key = self._s3_key(relative_path)
        try:
            resp = self._client.head_object(Bucket=self.bucket, Key=key)
            return {
                "etag": resp.get("ETag", "").strip('"'),
                "last_modified": resp.get("LastModified"),
                "metadata": resp.get("Metadata", {}),
                "content_length": resp.get("ContentLength", 0),
            }
        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return None
            raise

    def delete(self, relative_path: str) -> None:
        key = self._s3_key(relative_path)
        self._client.delete_object(Bucket=self.bucket, Key=key)
        logger.debug("Deleted s3://%s/%s", self.bucket, key)

    def list_objects(self) -> list[dict[str, Any]]:
        objects = []
        prefix = self.prefix.rstrip("/") + "/" if self.prefix else ""
        paginator = self._client.get_paginator("list_objects_v2")

        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                # Skip the prefix directory itself
                if key == prefix:
                    continue

                relative = self._relative_path(key)
                objects.append({
                    "key": key,
                    "relative_path": relative,
                    "etag": obj.get("ETag", "").strip('"'),
                    "last_modified": obj.get("LastModified"),
                    "size": obj.get("Size", 0),
                })

        return objects

    def get_metadata(self, relative_path: str) -> dict[str, str]:
        info = self.head(relative_path)
        if info is None:
            return {}
        return info.get("metadata", {})
