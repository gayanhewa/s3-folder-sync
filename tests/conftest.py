"""Shared test fixtures."""

from __future__ import annotations

import os

import boto3
import pytest
from moto import mock_aws

from s3_folder_sync.config import Config, MachineConfig, StorageConfig, SyncConfig
from s3_folder_sync.s3client import S3Client
from s3_folder_sync.state import StateDB

TEST_BUCKET = "test-sync-bucket"
TEST_REGION = "us-east-1"


@pytest.fixture
def tmp_watch_dir(tmp_path):
    """Create a temporary watch directory."""
    watch_dir = tmp_path / "workspace"
    watch_dir.mkdir()
    return watch_dir


@pytest.fixture
def config(tmp_watch_dir):
    """Create a test config."""
    return Config(
        storage=StorageConfig(
            endpoint="",
            bucket=TEST_BUCKET,
            prefix="test/",
            region=TEST_REGION,
        ),
        sync=SyncConfig(interval=10, debounce=0.1, delete_grace_period=0),
        machine=MachineConfig(id="test-machine"),
        watch_path=str(tmp_watch_dir),
    )


@pytest.fixture
def aws_env(monkeypatch):
    """Set dummy AWS credentials for moto."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", TEST_REGION)


@pytest.fixture
def mock_s3(aws_env):
    """Provide a mocked S3 service with a test bucket."""
    with mock_aws():
        client = boto3.client("s3", region_name=TEST_REGION)
        client.create_bucket(Bucket=TEST_BUCKET)
        yield client


@pytest.fixture
def s3client(config, mock_s3):
    """Provide an S3Client connected to mocked S3."""
    return S3Client(config)


@pytest.fixture
def state_db(config):
    """Provide a StateDB in a temp directory."""
    db = StateDB(config.db_path)
    yield db
    db.close()
