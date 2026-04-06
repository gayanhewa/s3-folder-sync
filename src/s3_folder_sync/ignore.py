"""File ignore pattern matching."""

from __future__ import annotations

import fnmatch
from pathlib import PurePosixPath


class IgnoreMatcher:
    """Match file paths against glob-style ignore patterns."""

    def __init__(self, patterns: list[str]) -> None:
        self.patterns = patterns

    def is_ignored(self, relative_path: str) -> bool:
        path = PurePosixPath(relative_path)
        for pattern in self.patterns:
            if fnmatch.fnmatch(relative_path, pattern):
                return True
            if fnmatch.fnmatch(path.name, pattern):
                return True
            # Check if any parent directory matches
            for parent in path.parents:
                if parent != PurePosixPath("."):
                    if fnmatch.fnmatch(str(parent), pattern):
                        return True
        return False
