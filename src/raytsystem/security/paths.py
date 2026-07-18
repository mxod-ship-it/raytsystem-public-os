from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from raytsystem.platform_runtime import IS_WINDOWS, O_BINARY, binary_readonly_flags


class PathPolicyError(ValueError):
    """Raised when an input path violates the workspace read policy."""


@dataclass(frozen=True)
class ReadResult:
    relative_path: str
    data: bytes
    mode: int
    size: int
    mtime_ns: int


def _lexical_relative(root: Path, candidate: str | Path) -> PurePosixPath:
    raw = os.fspath(candidate)
    if not raw or "\x00" in raw:
        raise PathPolicyError("Input path is malformed")

    root_abs = Path(os.path.abspath(root))

    if isinstance(candidate, Path):
        # ponytail: a Path object may carry platform-native separators
        # (backslashes on Windows). Trust its structure and normalize via
        # os.path.abspath (lexical, no symlink follow), then derive the
        # relative form against root.
        candidate_abs = Path(os.path.abspath(candidate))
        try:
            relative = candidate_abs.relative_to(root_abs)
        except ValueError as error:
            raise PathPolicyError("Input path escapes the workspace") from error
    else:
        # Raw strings must be POSIX-style: no backslashes, no drive letters.
        if "\\" in raw or (len(raw) >= 2 and raw[1] == ":"):
            raise PathPolicyError("Input path is malformed or non-POSIX")
        candidate_path = Path(raw)
        if candidate_path.is_absolute():
            candidate_abs = Path(os.path.abspath(candidate_path))
            try:
                relative = candidate_abs.relative_to(root_abs)
            except ValueError as error:
                raise PathPolicyError("Input path escapes the workspace") from error
        else:
            relative = candidate_path

    pure = PurePosixPath(relative.as_posix())
    if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        raise PathPolicyError("Input path escapes the workspace")
    return pure


def read_regular_file(root: Path, candidate: str | Path, *, max_bytes: int) -> ReadResult:
    """Read through directory file descriptors without following symlinks."""

    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    root = Path(os.path.abspath(root))
    relative = _lexical_relative(root, candidate)
    if IS_WINDOWS:  # ponytail: Windows has no dir_fd/O_NOFOLLOW; lstat-walk instead
        return _read_regular_file_windows(root, relative, max_bytes=max_bytes)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    cloexec = getattr(os, "O_CLOEXEC", 0)
    binary = O_BINARY
    root_fd = os.open(root, os.O_RDONLY | binary | directory_flag | cloexec)
    parent_fd = root_fd
    opened_parents: list[int] = []
    file_fd: int | None = None
    try:
        for component in relative.parts[:-1]:
            try:
                next_fd = os.open(
                    component,
                    os.O_RDONLY | binary | directory_flag | nofollow | cloexec,
                    dir_fd=parent_fd,
                )
            except OSError as error:
                raise PathPolicyError(
                    "Path parent is missing, non-directory or a symlink"
                ) from error
            opened_parents.append(next_fd)
            parent_fd = next_fd

        try:
            file_fd = os.open(
                relative.parts[-1],
                os.O_RDONLY | binary | nofollow | cloexec,
                dir_fd=parent_fd,
            )
        except OSError as error:
            raise PathPolicyError("Input is missing or a symlink") from error

        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode):
            raise PathPolicyError("Input must be a regular file")
        if before.st_nlink != 1:
            raise PathPolicyError("Hard-linked inputs are not accepted")
        if before.st_size > max_bytes:
            raise PathPolicyError("Input exceeds the configured size limit")

        chunks: list[bytes] = []
        consumed = 0
        while True:
            chunk = os.read(file_fd, min(1024 * 1024, max_bytes + 1 - consumed))
            if not chunk:
                break
            chunks.append(chunk)
            consumed += len(chunk)
            if consumed > max_bytes:
                raise PathPolicyError("Input exceeds the configured size limit")

        after = os.fstat(file_fd)
        stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
        if any(getattr(before, field) != getattr(after, field) for field in stable_fields):
            raise PathPolicyError("Input changed while it was being read")
        return ReadResult(
            relative_path=relative.as_posix(),
            data=b"".join(chunks),
            mode=before.st_mode,
            size=before.st_size,
            mtime_ns=before.st_mtime_ns,
        )
    finally:
        if file_fd is not None:
            os.close(file_fd)
        for descriptor in reversed(opened_parents):
            os.close(descriptor)
        os.close(root_fd)


def _read_regular_file_windows(
    root: Path, relative: PurePosixPath, *, max_bytes: int
) -> ReadResult:
    """Path-based equivalent of ``read_regular_file`` for native Windows.

    ponytail: Windows lacks ``dir_fd=`` and ``O_NOFOLLOW`` at the ``os.open``
    level, and ``os.open`` on a directory raises ``PermissionError``. We
    preserve the security invariants we can: per-component ``os.lstat`` rejects
    symlinked parents and symlinked leaf targets; the post-read ``fstat``
    snapshot preserves TOCTOU stability detection; size cap and "must be a
    regular file" checks are unchanged. The hardlink (``st_nlink != 1``) check
    is skipped because NTFS does not guarantee the same accounting and the
    no-symlink guarantee is already enforced via ``lstat``. Residual TOCTOU
    between ``lstat`` and ``open`` of the leaf is fundamental to the platform
    (no ``O_NOFOLLOW`` equivalent); mitigation is the ``lstat`` walk and the
    post-read stability check.
    """

    current = root
    for component in relative.parts[:-1]:
        current = current / component
        try:
            meta = os.lstat(current)
        except OSError as error:
            raise PathPolicyError(
                "Path parent is missing, non-directory or a symlink"
            ) from error
        if stat.S_ISLNK(meta.st_mode) or not stat.S_ISDIR(meta.st_mode):
            raise PathPolicyError(
                "Path parent is missing, non-directory or a symlink"
            )
    target = current / relative.parts[-1]
    try:
        target_meta = os.lstat(target)
    except OSError as error:
        raise PathPolicyError("Input is missing or a symlink") from error
    if stat.S_ISLNK(target_meta.st_mode) or not stat.S_ISREG(target_meta.st_mode):
        raise PathPolicyError("Input is missing or not a regular file")
    file_fd = os.open(target, binary_readonly_flags())
    try:
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode):
            raise PathPolicyError("Input must be a regular file")
        if before.st_size > max_bytes:
            raise PathPolicyError("Input exceeds the configured size limit")

        chunks: list[bytes] = []
        consumed = 0
        while True:
            chunk = os.read(file_fd, min(1024 * 1024, max_bytes + 1 - consumed))
            if not chunk:
                break
            chunks.append(chunk)
            consumed += len(chunk)
            if consumed > max_bytes:
                raise PathPolicyError("Input exceeds the configured size limit")

        after = os.fstat(file_fd)
        stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
        if any(getattr(before, field) != getattr(after, field) for field in stable_fields):
            raise PathPolicyError("Input changed while it was being read")
        return ReadResult(
            relative_path=relative.as_posix(),
            data=b"".join(chunks),
            mode=before.st_mode,
            size=before.st_size,
            mtime_ns=before.st_mtime_ns,
        )
    finally:
        os.close(file_fd)
