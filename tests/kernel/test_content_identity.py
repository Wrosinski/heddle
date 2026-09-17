"""
W4 core discriminators: independently authored raw observations and partitions.
"""

from __future__ import annotations

import os
import weakref
from dataclasses import asdict
from pathlib import Path

import pytest

from heddle.kernel.project_config import KernelError
from tests.content_identity_helpers import definition, source_api


def test_ac1_capture_returns_raw_typed_content(tmp_path):
    observer = source_api()
    (tmp_path / "raw").write_bytes(b"A\r\n\x00\xff")
    (tmp_path / "raw").chmod(0o644)
    (tmp_path / "link").symlink_to("missing-target")
    (tmp_path / "empty").mkdir()
    expected = (
        ("raw", "file", False, b"A\r\n\x00\xff"),
        ("link", "symlink", False, b"missing-target"),
        ("never-added.py", "missing", False, b""),
        ("empty", "directory", False, b""),
    )
    for path, kind, executable, raw in expected:
        assert asdict(observer.capture_path(tmp_path, path)) == {
            "path": path,
            "kind": kind,
            "executable": executable,
            "content": raw,
        }


def test_ac1_ac10_standalone_observation_releases_folded_captures(
    tmp_path, monkeypatch
):
    from heddle.kernel.source_manifest import ObservedPath

    observer = source_api()
    paths = ("a", "b", "c", "d")
    declared = definition(paths)

    def observe(shared):
        captured = []
        order = []

        def capture(_root, path):
            alive = sum(reference() is not None for reference in captured)
            assert alive == (
                len(captured) if shared is not None else min(1, len(captured))
            ), "standalone observation retained already-folded raw content"
            result = ObservedPath(path, "file", False, path.encode())
            captured.append(weakref.ref(result))
            order.append(path)
            return result

        monkeypatch.setattr(observer, "capture_path", capture)
        manifest = observer.observe_source(tmp_path, declared, observations=shared)
        assert order == list(paths)
        if shared is None:
            assert all(reference() is None for reference in captured)
        else:
            assert tuple(shared) == paths
            assert all(
                reference() is shared[path]
                for path, reference in zip(paths, captured, strict=True)
            )
        return manifest

    assert observe(None) == observe({})


def test_ac1_static_ancestor_symlink_refused_without_reading_target(
    tmp_path, monkeypatch
):
    observer = source_api()
    root = tmp_path / "host"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_bytes(b"not observed")
    (root / "alias").symlink_to(outside, target_is_directory=True)
    reads = []
    original = Path.read_bytes

    def read_bytes(path):
        if path.name == "secret":
            reads.append(path)
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    with pytest.raises(KernelError) as caught:
        observer.capture_path(root, "alias/secret")
    assert caught.value.code == "workspace-invalid"
    assert "alias" in caught.value.message
    assert reads == []


@pytest.mark.parametrize("failure", ["special", "unreadable"])
def test_ac1_unsupported_and_unreadable_required_inputs_fail_loud(
    tmp_path, monkeypatch, failure
):
    observer = source_api()
    source = tmp_path / "required"
    if failure == "special":
        os.mkfifo(source)
    else:
        source.write_bytes(b"required")
        original = Path.read_bytes

        def read_bytes(path):
            if path == source:
                raise PermissionError("injected required source denial")
            return original(path)

        monkeypatch.setattr(Path, "read_bytes", read_bytes)
    with pytest.raises(KernelError) as caught:
        observer.capture_path(tmp_path, "required")
    assert caught.value.code == "workspace-invalid"
    assert "required" in caught.value.message
