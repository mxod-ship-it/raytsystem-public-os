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

import errno
import gc
import os
import stat as stat_module
import subprocess
import time
from collections.abc import Sequence
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
    "descend_directory",
    "fchmod",
    "fsync_directory",
    "fsync_file",
    "hardlink_under",
    "kill_process_tree",
    "lstat_under",
    "mkdir_under",
    "open_file_readonly",
    "open_under",
    "rename_under",
    "replace_under",
    "rmdir_under",
    "symlink_under",
    "unlink_under",
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


# ---------------------------------------------------------------------------
# Path-based helpers (dual-mode replacements for ``dir_fd=`` walks)
#
# raytsystem's POSIX code opens a parent directory file descriptor, walks
# through it with ``dir_fd=`` and ``O_NOFOLLOW`` so symlink escape and
# TOCTOU swap cannot replace the entry mid-operation. Windows has no
# ``dir_fd=`` support and ``os.open`` on a directory raises
# ``PermissionError``; the equivalent invariants are preserved through a
# per-component ``lstat`` walk that rejects symlinks, plus the atomic
# guarantees of ``os.replace``. Helpers are dual-mode so call sites stay
# identical across platforms.
# ---------------------------------------------------------------------------


def _directory_flags() -> int:
    return os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC


def _open_parent_fd(parent: Path) -> int:
    return os.open(parent, _directory_flags())


def descend_directory(root: Path, components: Sequence[str]) -> Path:
    """Walk through ``root/components/...`` rejecting symlinks and non-dirs.

    Returns the resolved leaf ``Path``. On POSIX each component is opened
    with ``O_NOFOLLOW | O_DIRECTORY`` via ``dir_fd=`` for TOCTOU safety;
    on Windows the same invariant is enforced with a per-component
    ``lstat`` check (no kernel-level ``O_NOFOLLOW`` exists).
    """

    if IS_WINDOWS:
        current = root
        for component in components:
            current = current / component
            try:
                meta = os.lstat(current)
            except OSError as error:
                raise OSError(
                    errno.ENOTDIR,
                    "Path parent is missing, non-directory or a symlink",
                    str(current),
                ) from error
            if stat_module.S_ISLNK(meta.st_mode) or not stat_module.S_ISDIR(
                meta.st_mode
            ):
                raise OSError(
                    errno.ENOTDIR,
                    "Path parent is missing, non-directory or a symlink",
                    str(current),
                )
        return current
    current_fd = os.open(root, _directory_flags())
    opened: list[int] = [current_fd]
    try:
        for component in components:
            next_fd = os.open(
                component, _directory_flags(), dir_fd=current_fd
            )
            opened.append(next_fd)
            current_fd = next_fd
        return root.joinpath(*components) if components else root
    finally:
        for descriptor in reversed(opened):
            with suppress(OSError):
                os.close(descriptor)


def open_under(
    parent: Path, name: str, flags: int, mode: int = 0o777
) -> int:
    """Open file ``name`` inside ``parent`` directory, TOCTOU-safe.

    POSIX: opens ``parent`` with ``O_DIRECTORY | O_NOFOLLOW`` and opens
    ``name`` with ``dir_fd=``. Windows: path-based with an ``lstat``
    pre-check (no kernel ``O_NOFOLLOW``); ``O_BINARY`` is auto-added.
    """

    if IS_WINDOWS:
        target = parent / name
        # lstat pre-check rejects symlink substitution attacks when the
        # file already exists. For O_CREAT creates of a new entry there
        # is nothing to lstat; fall through and let os.open raise the
        # canonical FileNotFoundError / FileExistsError.
        try:
            meta = os.lstat(target)
        except FileNotFoundError:
            pass
        except OSError as error:
            raise error
        else:
            if stat_module.S_ISLNK(meta.st_mode):
                raise OSError(
                    errno.ELOOP,
                    "Symlink rejected by path policy",
                    str(target),
                )
        return os.open(target, flags | O_BINARY, mode)
    parent_fd = _open_parent_fd(parent)
    try:
        return os.open(name, flags | O_CLOEXEC, mode, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)


def lstat_under(parent: Path, name: str) -> os.stat_result:
    """``os.stat(name, dir_fd=parent, follow_symlinks=False)`` equivalent."""

    if IS_WINDOWS:
        return os.stat(parent / name, follow_symlinks=False)
    parent_fd = _open_parent_fd(parent)
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    finally:
        os.close(parent_fd)


def unlink_under(parent: Path, name: str) -> None:
    """``os.unlink(name, dir_fd=parent)`` equivalent."""

    if IS_WINDOWS:
        os.unlink(parent / name)
        return
    parent_fd = _open_parent_fd(parent)
    try:
        os.unlink(name, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)


def rmdir_under(parent: Path, name: str) -> None:
    """``os.rmdir(name, dir_fd=parent)`` equivalent."""

    if IS_WINDOWS:
        os.rmdir(parent / name)
        return
    parent_fd = _open_parent_fd(parent)
    try:
        os.rmdir(name, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)


def mkdir_under(parent: Path, name: str, mode: int = 0o755) -> None:
    """``os.mkdir(name, mode, dir_fd=parent)`` equivalent.

    Windows: ``os.mkdir`` accepts ``mode`` for symmetry but does not store
    POSIX bits; ACL inheritance governs access.
    """

    target = parent / name
    os.mkdir(target, mode)
    if IS_POSIX:
        os.chmod(target, mode, follow_symlinks=False)


def replace_under(
    src_parent: Path,
    src_name: str,
    dst_parent: Path,
    dst_name: str,
) -> None:
    """``os.replace(src, dst, src_dir_fd=..., dst_dir_fd=...)`` equivalent."""

    if IS_WINDOWS:
        os.replace(src_parent / src_name, dst_parent / dst_name)
        return
    src_fd = _open_parent_fd(src_parent)
    try:
        dst_fd = _open_parent_fd(dst_parent)
        try:
            os.replace(
                src_name, dst_name, src_dir_fd=src_fd, dst_dir_fd=dst_fd
            )
        finally:
            os.close(dst_fd)
    finally:
        os.close(src_fd)


def atomic_replace(src: Path, dst: Path, *, attempts: int = 20) -> None:
    """``os.replace(src, dst)`` with Windows-friendly retry for transient locks.

    On Windows, real-time antivirus and lingering readers raise
    ``PermissionError`` (WinError 5/32) when replacing an existing file. A
    short backoff lets the lock clear instead of failing the whole rebuild.
    When ``os.replace`` keeps failing, fall back to ``MoveFileExW`` with
    ``MOVEFILE_REPLACE_EXISTING``; unlike ``rename``, it can replace a file
    that another handle holds open for shared read/write (sqlite does).
    """

    if not IS_WINDOWS:
        os.replace(src, dst)
        return
    # ponytail: a just-closed sqlite connection keeps its handle until GC on
    # Windows; collect first so replace is not blocked by a stale reader/writer.
    gc.collect()
    last: BaseException | None = None
    for attempt in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError as error:
            last = error
            gc.collect()
            time.sleep(0.05 * (attempt + 1))
    # ponytail: MoveFileExW can replace a file held open by a shared reader
    # (sqlite), which plain rename cannot on Windows.
    if _move_file_replace(str(src), str(dst)):
        return
    raise last if last is not None else OSError("atomic_replace failed")


def _move_file_replace(src: str, dst: str) -> bool:
    """Best-effort ``MoveFileExW(src, dst, MOVEFILE_REPLACE_EXISTING)``.

    Returns True on success. Falls back to False if ``ctypes``/kernel32 is
    unavailable so the caller can surface the original error.
    """

    try:
        import ctypes
    except ImportError:  # pragma: no cover - ctypes is stdlib on every target
        return False
    kernel32 = getattr(ctypes.windll, "kernel32", None)
    if kernel32 is None:
        return False
    move_file_ex = kernel32.MoveFileExW
    move_file_ex.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
    move_file_ex.restype = ctypes.c_int
    MOVEFILE_REPLACE_EXISTING = 0x1
    result = move_file_ex(src, dst, MOVEFILE_REPLACE_EXISTING)
    if result:
        return True
    last_error = ctypes.GetLastError()
    # ponytail: only swallow the sharing-violation path; anything else is real.
    if last_error not in (0, 5, 32, 183):
        return False
    # Retry once more after a brief pause in case the reader is releasing.
    time.sleep(0.1)
    return bool(move_file_ex(src, dst, MOVEFILE_REPLACE_EXISTING))


def rename_under(
    src_parent: Path,
    src_name: str,
    dst_parent: Path,
    dst_name: str,
) -> None:
    """``os.rename(src, dst, src_dir_fd=..., dst_dir_fd=...)`` equivalent.

    Raises ``FileExistsError`` if the destination already exists on Windows
    (matches POSIX ``renameat2`` ``RENAME_NOREPLACE`` semantics used by the
    no-replace migration path).
    """

    if IS_WINDOWS:
        dst_path = dst_parent / dst_name
        if os.path.lexists(dst_path):
            raise FileExistsError(
                errno.EEXIST,
                "Destination already exists",
                str(dst_path),
            )
        os.rename(src_parent / src_name, dst_path)
        return
    src_fd = _open_parent_fd(src_parent)
    try:
        dst_fd = _open_parent_fd(dst_parent)
        try:
            os.rename(src_name, dst_name, src_dir_fd=src_fd, dst_dir_fd=dst_fd)
        finally:
            os.close(dst_fd)
    finally:
        os.close(src_fd)


def symlink_under(target: str, parent: Path, link: str) -> None:
    """``os.symlink(target, link, dir_fd=parent)`` equivalent."""

    if IS_WINDOWS:
        os.symlink(target, parent / link)
        return
    parent_fd = _open_parent_fd(parent)
    try:
        os.symlink(target, link, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)


def hardlink_under(
    src_parent: Path,
    src_name: str,
    dst_parent: Path,
    dst_name: str,
) -> None:
    """``os.link(src, dst, src_dir_fd=..., dst_dir_fd=...)`` equivalent."""

    if IS_WINDOWS:
        os.link(src_parent / src_name, dst_parent / dst_name)
        return
    src_fd = _open_parent_fd(src_parent)
    try:
        dst_fd = _open_parent_fd(dst_parent)
        try:
            os.link(src_name, dst_name, src_dir_fd=src_fd, dst_dir_fd=dst_fd)
        finally:
            os.close(dst_fd)
    finally:
        os.close(src_fd)


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
