"""Crash-safe file writes: write to a temp file, fsync, then atomically replace.

A bare ``Path.write_bytes`` / ``Path.write_text`` is not crash-safe. If the
process dies or the disk fills partway through, the target is left torn (half an
image) or corrupt (a sidecar that no longer parses as JSON). A concurrent reader
can also observe the file mid-write. Either way a later read fails or, worse,
silently sees garbage.

These helpers close both gaps. They write the full content to a temporary file
in the SAME directory as the target, ``flush()`` + ``os.fsync()`` that file so
the bytes are on stable storage, then ``os.replace(tmp, path)``. ``os.replace``
is an atomic rename on POSIX when source and destination are on the same
filesystem (which they are, since the temp file is a sibling), so a reader sees
either the old file or the new file in full, never a partial one. On any failure
the temp file is removed and the error is re-raised, so a crash mid-write leaves
neither a torn target nor an orphan temp file behind.

The temp file is created with ``tempfile.mkstemp`` (``O_CREAT | O_EXCL``), so its
name cannot collide with an existing file or a concurrent writer's temp file.

This module imports nothing outside the standard library.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically (temp file + fsync + os.replace).

    Creates ``path.parent`` if it does not exist (matching the prior behavior of
    the call sites). The target is only ever replaced as a whole: a reader sees
    either the complete old content or the complete new content, never a partial
    write. On any error the temp file is removed and the error re-raised.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # mkstemp uses O_CREAT|O_EXCL, so the name is collision-proof against an
    # existing file or a concurrent writer's temp file. Same directory as the
    # target keeps it on one filesystem so the os.replace below is atomic.
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            # Force the bytes to stable storage before the rename, so a crash
            # right after os.replace cannot surface a target that points at
            # not-yet-flushed data.
            fh.flush()
            os.fsync(fh.fileno())
        # mkstemp creates the temp file mode 0600 and os.replace preserves the
        # source mode, so without this a replaced file would silently lose its
        # permissions and a new file would be owner-only -- a served asset must
        # stay readable the way the prior write_bytes/write_text left it (0644
        # under the usual umask). Preserve an existing target's mode on overwrite.
        try:
            mode = os.stat(path).st_mode & 0o777
        except FileNotFoundError:
            mode = 0o644
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        # No torn target and no orphan temp left behind. The original target (if
        # any) is untouched, since os.replace either fully succeeded or never ran.
        tmp.unlink(missing_ok=True)
        raise


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    """Write ``text`` to ``path`` atomically, encoding with ``encoding``.

    A thin wrapper over ``atomic_write_bytes`` so text and bytes share the exact
    same temp-file + fsync + os.replace crash-safety guarantees.
    """
    atomic_write_bytes(path, text.encode(encoding))
