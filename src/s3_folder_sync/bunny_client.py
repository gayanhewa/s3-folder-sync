"""Bunny.net Edge Storage client."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

import requests

from s3_folder_sync.config import Config

logger = logging.getLogger(__name__)

META_PREFIX = ".s3sync-meta"


class BunnyClient:
    """Client for Bunny.net Edge Storage API.

    Implements the same interface as S3Client so the sync engine
    can use either backend.
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        self.zone_name = config.storage.bucket
        self.prefix = config.storage.prefix
        self.base_url = config.storage.endpoint.rstrip("/")
        self.access_key = config.storage.access_key or config.storage.secret_key
        self._session = requests.Session()
        self._session.headers["AccessKey"] = self.access_key

    def _file_url(self, relative_path: str) -> str:
        if self.prefix:
            return f"{self.base_url}/{self.zone_name}/{self.prefix.strip('/')}/{relative_path}"
        return f"{self.base_url}/{self.zone_name}/{relative_path}"

    def _meta_url(self, relative_path: str) -> str:
        return f"{self.base_url}/{self.zone_name}/{META_PREFIX}/{relative_path}.json"

    def _list_url(self, path: str = "") -> str:
        if self.prefix:
            return f"{self.base_url}/{self.zone_name}/{self.prefix.strip('/')}/{path}"
        return f"{self.base_url}/{self.zone_name}/{path}"

    def upload(
        self,
        local_path: Path,
        relative_path: str,
        content_hash: str,
        machine_id: str,
        synced_at: str,
    ) -> str:
        url = self._file_url(relative_path)
        with open(local_path, "rb") as f:
            data = f.read()

        resp = self._session.put(
            url,
            data=data,
            headers={"Content-Type": "application/octet-stream"},
        )
        resp.raise_for_status()

        # Compute checksum as our "etag"
        etag = hashlib.sha256(data).hexdigest()

        # Store metadata as sidecar
        meta = {
            "source-hash": content_hash,
            "machine-id": machine_id,
            "synced-at": synced_at,
            "etag": etag,
        }
        meta_resp = self._session.put(
            self._meta_url(relative_path),
            data=json.dumps(meta).encode(),
            headers={"Content-Type": "application/json"},
        )
        meta_resp.raise_for_status()

        logger.debug("Uploaded %s (etag=%s)", relative_path, etag[:12])
        return etag

    def download(self, relative_path: str, local_path: Path) -> dict[str, str]:
        url = self._file_url(relative_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)

        resp = self._session.get(url)
        resp.raise_for_status()

        with open(local_path, "wb") as f:
            f.write(resp.content)

        metadata = self._get_sidecar_meta(relative_path)
        logger.debug("Downloaded %s", relative_path)
        return metadata

    def head(self, relative_path: str) -> dict[str, Any] | None:
        url = self._file_url(relative_path)
        resp = self._session.head(url)

        if resp.status_code == 404:
            return None
        resp.raise_for_status()

        meta = self._get_sidecar_meta(relative_path)
        content_length = int(resp.headers.get("Content-Length", 0))

        return {
            "etag": meta.get("etag", ""),
            "last_modified": resp.headers.get("Last-Modified"),
            "metadata": meta,
            "content_length": content_length,
        }

    def delete(self, relative_path: str) -> None:
        url = self._file_url(relative_path)
        resp = self._session.delete(url)
        if resp.status_code != 404:
            resp.raise_for_status()

        # Also delete sidecar metadata
        meta_url = self._meta_url(relative_path)
        meta_resp = self._session.delete(meta_url)
        # Ignore 404 on meta delete

        logger.debug("Deleted %s", relative_path)

    def list_objects(self) -> list[dict[str, Any]]:
        objects = []
        self._list_recursive("", objects)
        return objects

    def _list_recursive(self, path: str, objects: list[dict[str, Any]]) -> None:
        url = self._list_url(path)
        resp = self._session.get(url)
        resp.raise_for_status()

        items = resp.json()
        for item in items:
            obj_name = item.get("ObjectName", "")
            full_path = path + obj_name if path else obj_name

            # Skip metadata sidecar directory
            if obj_name == META_PREFIX or full_path.startswith(META_PREFIX):
                continue

            if item.get("IsDirectory", False):
                self._list_recursive(full_path + "/", objects)
            else:
                checksum = item.get("Checksum", "").lower()
                meta = self._get_sidecar_meta(full_path)
                etag = meta.get("etag", checksum)

                objects.append({
                    "key": full_path,
                    "relative_path": full_path,
                    "etag": etag,
                    "last_modified": item.get("LastChanged"),
                    "size": item.get("Length", 0),
                })

    def get_metadata(self, relative_path: str) -> dict[str, str]:
        return self._get_sidecar_meta(relative_path)

    def _get_sidecar_meta(self, relative_path: str) -> dict[str, str]:
        url = self._meta_url(relative_path)
        try:
            resp = self._session.get(url)
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        return {}
