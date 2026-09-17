"""
Tests for path overlap computation between diff and milestone-owned paths.

Covers the three overlap statuses: matched, zero-overlap, unassessed.
"""

from __future__ import annotations

import pytest

REQUIRES_IMPL = pytest.mark.skipif(False, reason="gate package implemented")


class TestOverlapComputation:
    @REQUIRES_IMPL
    def test_matched_when_paths_intersect(self) -> None:
        from heddle.gate.overlap import compute_overlap

        changed = ["src/example/sample/core.py", "config/sample/default.yaml"]
        owned = ["src/example/sample/core.py", "src/example/sample/models.py"]
        result = compute_overlap(changed, owned)
        assert result.status == "matched"
        assert "src/example/sample/core.py" in result.files

    @REQUIRES_IMPL
    def test_zero_overlap_when_no_intersection(self) -> None:
        from heddle.gate.overlap import compute_overlap

        changed = ["src/example/other/module.py"]
        owned = ["src/example/sample/core.py"]
        result = compute_overlap(changed, owned)
        assert result.status == "zero-overlap"
        assert result.files == ()

    @REQUIRES_IMPL
    def test_unassessed_when_no_owned_paths(self) -> None:
        from heddle.gate.overlap import compute_overlap

        changed = ["src/example/sample/core.py"]
        owned: list[str] = []
        result = compute_overlap(changed, owned)
        assert result.status == "unassessed"

    @REQUIRES_IMPL
    def test_directory_prefix_matching(self) -> None:
        """Changed file under an owned directory counts as overlap."""
        from heddle.gate.overlap import compute_overlap

        changed = ["src/example/sample/subdir/deep.py"]
        owned = ["src/example/sample/"]
        result = compute_overlap(changed, owned)
        assert result.status == "matched"
        assert "src/example/sample/subdir/deep.py" in result.files

    @REQUIRES_IMPL
    def test_owned_file_under_changed_directory(self) -> None:
        """Owned file under a changed directory counts as overlap."""
        from heddle.gate.overlap import compute_overlap

        changed = ["src/example/sample/"]
        owned = ["src/example/sample/core.py"]
        result = compute_overlap(changed, owned)
        assert result.status == "matched"

    @REQUIRES_IMPL
    def test_empty_changed_files(self) -> None:
        from heddle.gate.overlap import compute_overlap

        result = compute_overlap([], ["src/example/sample/core.py"])
        assert result.status == "zero-overlap"
