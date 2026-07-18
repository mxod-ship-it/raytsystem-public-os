"""Single registry for platform-specific OS primitives.

raytsystem is POSIX-first; native Windows support is additive. Every
platform divergence lives here so call sites import a helper instead of
re-checking ``os.name``. Detection runs once at import; helpers are
module-level functions (namespace, not a class) to keep the surface area
flat and test-friendly.

Environment overrides (read once at import):

* ``RAYTSYSTEM_PLATFORM_ASSUME`` - force ``"windows"`` or ``"posix"`` for
  tests that need to exercise the other branch on the wrong host.
* ``RAYTSYSTEM_PLATFORM_DISABLE_FSYNC`` - truthy (1/true/yes) disables all
  ``fsync`` calls, including directory fsyncs. Useful for fast CI runs and
  for scratch spaces where durability is not required. Never set in
  production ledgers.

The module never raises ``NotImplementedError``. Operations that the
current platform cannot honor are silent no-ops (with the invariant they
would have enforced already covered another way - e.g. ACL inheritance on
NTFS substitutes for POSIX mode bits).
"""

from __future__ import annotations

import os
import subprocess
from contextlib import suppress
from pathlib import Path

__all__ = [
    "IS_POSIX",
    "IS_WINDOWS",
    "O_BINARY",
    "O_CLOEXEC",
    "O_DIRECTORY",
    "O_NOFOLLOW",
    "binary_readonly_flags",
    "chmod_private",
    "fchmod",
    "fsync_directory",
    "fsync_file",
    "kill_process_tree",
    "open_file_readonly",
]


def _resolve_platform() -> str:
    forced = os.environ.get("RAYTSYSTEM_PLATFORM_ASSUME", "").strip().lower()
    if forced in {"windows", "posix"}:
        return forced
    return "windows" if os.name == "nt" else "posix"


_CURRENT_PLATFORM = _resolve_platform()
IS_WINDOWS: bool = _CURRENT_PLATFORM == "windows"
IS_POSIX: bool = not IS_WINDOWS


def _truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


_DISABLE_FSYNC: bool = _truthy(os.environ.get("RAYTSYSTEM_PLATFORM_DISABLE_FSYNC"))

O_BINARY: int = getattr(os, "O_BINARY", 0)
O_NOFOLLOW: int = getattr(os, "O_NOFOLLOW", 0)
O_CLOEXEC: int = getattr(os, "O_CLOEXEC", 0)
O_DIRECTORY: int = getattr(os, "O_DIRECTORY", 0)


def binary_readonly_flags(*, nofollow: bool = False) -> int:
    """Return ``os.open`` flags for a binary, read-only, CLOEXEC file handle.

    On Windows the result includes ``O_BINARY`` (without it Python opens
    descriptors in text mode and mangles ``\\r\\n`` / treats ``\\x1a`` as
    EOF). ``O_NOFOLLOW`` is opt-in because directory-open callers that walk
    parent-by-parent use it on leaf opens only.
    """

    flags = os.O_RDONLY | O_BINARY | O_CLOEXEC
    if nofollow:
        flags |= O_NOFOLLOW
    return flags


def open_file_readonly(path: Path, *, nofollow: bool = False) -> int:
    """Open a regular file for binary read and return its file descriptor."""

    return os.open(path, binary_readonly_flags(nofollow=nofollow))


def fsync_directory(path: Path) -> None:
    """Best-effort directory fsync after atomic renames.

    No-op on Windows (NTFS rename durability differs; there is no
    userspace equivalent of opening a directory for fsync). Also a no-op
    when ``RAYTSYSTEM_PLATFORM_DISABLE_FSYNC`` is set.
    """

    if IS_WINDOWS or _DISABLE_FSYNC:
        return
    fd = os.open(path, os.O_RDONLY | O_DIRECTORY | O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def fsync_file(fd: int) -> None:
    """fsync a writable file descriptor. No-op when fsyncs are disabled."""

    if _DISABLE_FSYNC:
        return
    os.fsync(fd)


def fchmod(fd: int, mode: int) -> None:
    """Apply a POSIX mode to an open descriptor.

    No-op on Windows: NTFS does not store POSIX mode bits, and access is
    governed by the inherited ACL of the parent directory.
    """

    if IS_WINDOWS:
        return
    os.fchmod(fd, mode)


def chmod_private(path: Path, mode: int = 0o700) -> None:
    """Mark a directory or file private to the owner.

    On POSIX this calls ``os.chmod`` with ``follow_symlinks=False`` so a
    symlinked path cannot be followed. On Windows the plain ``os.chmod``
    is used (``follow_symlinks`` is not implemented and raises
    ``NotImplementedError``); NTFS inherits ACL from the parent and the
    resulting mode bits are advisory only.
    """

    if IS_WINDOWS:
        os.chmod(path, mode)
        return
    os.chmod(path, mode, follow_symlinks=False)


def kill_process_tree(
    process: subprocess.Popen[bytes],
    *,
    timeout: float = 5.0,
) -> int:
    """Stop a process and (where possible) its children.

    On POSIX the whole process group is signaled via ``os.killpg``; on
    Windows we fall back to ``terminate`` then ``kill`` because process
    groups require a console and are not portable. Returns the process
    exit code.
    """

    import signal as _signal

    if IS_POSIX:
        with suppress(OSError):
            os.killpg(os.getpgid(process.pid), _signal.SIGTERM)
        try:
            return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            with suppress(OSError):
                os.killpg(os.getpgid(process.pid), _signal.SIGKILL)
            return process.wait(timeout=timeout)
    process.terminate()
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        return process.wait(timeout=timeout)


def _self_check() -> None:
    """Lightweight invariant check executed via ``python -c`` or in tests.

    Confirms that the active platform constant matches ``os.name`` when no
    override is set, and that all the ``getattr`` flags resolved to ints.
    """

    forced = os.environ.get("RAYTSYSTEM_PLATFORM_ASSUME", "").strip().lower()
    if not forced:
        assert (os.name == "nt") == IS_WINDOWS, "platform detection mismatch"
    assert isinstance(O_BINARY, int)
    assert isinstance(O_NOFOLLOW, int)
    assert isinstance(O_CLOEXEC, int)
    assert isinstance(O_DIRECTORY, int)


if __name__ == "__main__":  # pragma: no cover
    _self_check()
    print(f"platform={_CURRENT_PLATFORM} fsync_disabled={_DISABLE_FSYNC}")
