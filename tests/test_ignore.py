"""Tests for ignore pattern matching."""

from s3_folder_sync.ignore import IgnoreMatcher


class TestIgnoreMatcher:
    def test_exact_filename(self):
        m = IgnoreMatcher([".DS_Store"])
        assert m.is_ignored(".DS_Store")
        assert m.is_ignored("subdir/.DS_Store")
        assert not m.is_ignored("readme.md")

    def test_extension_glob(self):
        m = IgnoreMatcher(["*.tmp"])
        assert m.is_ignored("file.tmp")
        assert m.is_ignored("dir/file.tmp")
        assert not m.is_ignored("file.txt")

    def test_directory_glob(self):
        m = IgnoreMatcher([".git/**"])
        assert m.is_ignored(".git/config")
        assert m.is_ignored(".git/objects/abc123")
        assert not m.is_ignored("readme.md")

    def test_node_modules(self):
        m = IgnoreMatcher(["node_modules/**"])
        assert m.is_ignored("node_modules/package/index.js")
        assert not m.is_ignored("src/index.js")

    def test_s3sync_ignored(self):
        m = IgnoreMatcher([".s3sync/**"])
        assert m.is_ignored(".s3sync/state.db")
        assert m.is_ignored(".s3sync/config.toml")

    def test_multiple_patterns(self):
        m = IgnoreMatcher([".DS_Store", "*.tmp", ".git/**"])
        assert m.is_ignored(".DS_Store")
        assert m.is_ignored("test.tmp")
        assert m.is_ignored(".git/HEAD")
        assert not m.is_ignored("notes.md")

    def test_empty_patterns(self):
        m = IgnoreMatcher([])
        assert not m.is_ignored("anything.txt")

    def test_pycache(self):
        m = IgnoreMatcher(["__pycache__/**", "*.pyc"])
        assert m.is_ignored("__pycache__/module.cpython-311.pyc")
        assert m.is_ignored("src/module.pyc")
