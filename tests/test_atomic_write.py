"""Unit tests for the crash-safe write helpers. No database, no network.

These prove the contract the rest of the app relies on: the target is replaced as
a whole (never torn), a failure mid-write leaves the original target intact and no
orphan temp file behind, and the parent directory is created on demand.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from brand_ops.atomic_write import atomic_write_bytes, atomic_write_text


def test_write_bytes_produces_exact_content(tmp_path: Path) -> None:
    target = tmp_path / "image.png"
    atomic_write_bytes(target, b"\x89PNG\r\n\x1a\n binary")
    assert target.read_bytes() == b"\x89PNG\r\n\x1a\n binary"


def test_write_text_produces_exact_content(tmp_path: Path) -> None:
    target = tmp_path / "sidecar.json"
    atomic_write_text(target, '{"k": "v", "u": "é"}')
    assert target.read_text(encoding="utf-8") == '{"k": "v", "u": "é"}'


def test_write_text_honors_encoding(tmp_path: Path) -> None:
    target = tmp_path / "latin.txt"
    atomic_write_text(target, "café", encoding="latin-1")
    assert target.read_bytes() == "café".encode("latin-1")


def test_overwrite_replaces_content(tmp_path: Path) -> None:
    target = tmp_path / "asset.bin"
    atomic_write_bytes(target, b"old content")
    atomic_write_bytes(target, b"new content")
    assert target.read_bytes() == b"new content"


def test_no_leftover_temp_files_after_write(tmp_path: Path) -> None:
    target = tmp_path / "only.json"
    atomic_write_text(target, "{}")
    # The directory holds exactly the target and nothing else (no .tmp sibling).
    assert [p.name for p in tmp_path.iterdir()] == ["only.json"]


def test_no_leftover_temp_files_after_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "only.bin"
    atomic_write_bytes(target, b"first")
    atomic_write_bytes(target, b"second")
    assert [p.name for p in tmp_path.iterdir()] == ["only.bin"]


def test_parent_directory_is_created(tmp_path: Path) -> None:
    target = tmp_path / "deep" / "nested" / "dir" / "file.png"
    assert not target.parent.exists()
    atomic_write_bytes(target, b"data")
    assert target.read_bytes() == b"data"


def test_failure_leaves_existing_target_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "asset.bin"
    atomic_write_bytes(target, b"original")

    def boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="simulated replace failure"):
        atomic_write_bytes(target, b"replacement")

    # The original is untouched and no orphan temp file is left behind.
    assert target.read_bytes() == b"original"
    assert [p.name for p in tmp_path.iterdir()] == ["asset.bin"]


def test_failure_when_target_absent_leaves_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "asset.bin"

    def boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="simulated replace failure"):
        atomic_write_bytes(target, b"replacement")

    # The target was never created and no orphan temp file remains.
    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


def test_text_failure_leaves_existing_target_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "sidecar.json"
    atomic_write_text(target, '{"v": 1}')

    def boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="simulated replace failure"):
        atomic_write_text(target, '{"v": 2}')

    assert target.read_text(encoding="utf-8") == '{"v": 1}'
    assert [p.name for p in tmp_path.iterdir()] == ["sidecar.json"]


def test_new_file_is_world_readable_not_owner_only(tmp_path: Path) -> None:
    # mkstemp would leave the file 0600; a served asset must stay readable the way
    # the prior write_bytes left it, so a brand-new file lands at 0644.
    target = tmp_path / "served.png"
    atomic_write_bytes(target, b"bytes")
    assert target.stat().st_mode & 0o777 == 0o644


def test_overwrite_preserves_existing_mode(tmp_path: Path) -> None:
    # An overwrite must not silently change permissions: if an operator tightened
    # a file to 0640, rewriting it keeps 0640 rather than resetting it.
    target = tmp_path / "asset.bin"
    atomic_write_bytes(target, b"first")
    os.chmod(target, 0o640)
    atomic_write_bytes(target, b"second")
    assert target.read_bytes() == b"second"
    assert target.stat().st_mode & 0o777 == 0o640
