"""Tests for S3 client wrapper."""

from pathlib import Path

from s3_folder_sync.s3client import S3Client


class TestS3Client:
    def test_upload_and_download(self, s3client, tmp_watch_dir):
        # Create a local file
        local_file = tmp_watch_dir / "test.md"
        local_file.write_text("hello world")

        # Upload
        etag = s3client.upload(
            local_path=local_file,
            relative_path="test.md",
            content_hash="abc123",
            machine_id="mac-1",
            synced_at="2024-01-01T00:00:00",
        )
        assert etag

        # Download to a different path
        download_path = tmp_watch_dir / "downloaded.md"
        metadata = s3client.download("test.md", download_path)
        assert download_path.read_text() == "hello world"
        assert metadata.get("source-hash") == "abc123"
        assert metadata.get("machine-id") == "mac-1"

    def test_head(self, s3client, tmp_watch_dir):
        local_file = tmp_watch_dir / "test.md"
        local_file.write_text("content")
        s3client.upload(local_file, "test.md", "hash1", "mac-1", "ts1")

        info = s3client.head("test.md")
        assert info is not None
        assert "etag" in info
        assert info["metadata"]["source-hash"] == "hash1"

    def test_head_nonexistent(self, s3client):
        assert s3client.head("nope.txt") is None

    def test_delete(self, s3client, tmp_watch_dir):
        local_file = tmp_watch_dir / "test.md"
        local_file.write_text("content")
        s3client.upload(local_file, "test.md", "h1", "m1", "t1")

        s3client.delete("test.md")
        assert s3client.head("test.md") is None

    def test_list_objects(self, s3client, tmp_watch_dir):
        for name in ["a.md", "b.md", "sub/c.md"]:
            path = tmp_watch_dir / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"content of {name}")
            s3client.upload(path, name, f"hash-{name}", "mac-1", "ts1")

        objects = s3client.list_objects()
        paths = {o["relative_path"] for o in objects}
        assert paths == {"a.md", "b.md", "sub/c.md"}

    def test_get_metadata(self, s3client, tmp_watch_dir):
        local_file = tmp_watch_dir / "test.md"
        local_file.write_text("content")
        s3client.upload(local_file, "test.md", "myhash", "mac-2", "2024-01-01")

        meta = s3client.get_metadata("test.md")
        assert meta["source-hash"] == "myhash"
        assert meta["machine-id"] == "mac-2"

    def test_prefix_handling(self, s3client):
        assert s3client._s3_key("file.md") == "test/file.md"
        assert s3client._relative_path("test/file.md") == "file.md"

    def test_binary_file(self, s3client, tmp_watch_dir):
        local_file = tmp_watch_dir / "image.png"
        local_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        s3client.upload(local_file, "image.png", "binhash", "mac-1", "ts1")

        download_path = tmp_watch_dir / "downloaded.png"
        s3client.download("image.png", download_path)
        assert download_path.read_bytes() == local_file.read_bytes()
