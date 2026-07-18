from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import stat
import sys
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from raytsystem.io import ensure_safe_directory, write_bytes_atomic
from raytsystem.platform_runtime import (
    IS_WINDOWS,
    O_BINARY,
    descend_directory,
    fsync_directory,
    lstat_under,
    rename_under,
)
from raytsystem.security.paths import PathPolicyError, read_regular_file

# Compatibility-only identifiers. They are intentionally isolated here so the
# retired namespace never leaks into product copy or newly-created workspaces.
_LEGACY_CONFIG = Path("config") / "agentos.toml"
_CURRENT_CONFIG = Path("config") / "raytsystem.toml"
_LEGACY_STATE = Path(".agentos")
_CURRENT_STATE = Path(".raytsystem")
_METADATA_NAMES = frozenset({"installation.json", "source-map.json", "manifest.json"})


class BrandMigrationError(RuntimeError):
    """A legacy workspace cannot be migrated without risking user data."""


@dataclass(frozen=True)
class BrandMigrationResult:
    migrated: bool
    backup_path: str | None
    changed_paths: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "migrated": self.migrated,
            "backup_path": self.backup_path,
            "changed_paths": list(self.changed_paths),
        }


@dataclass(frozen=True)
class _PathIdentity:
    device: int
    inode: int
    mode: int
    size: int
    modified_ns: int

    @classmethod
    def from_stat(cls, metadata: os.stat_result) -> _PathIdentity:
        return cls(
            device=metadata.st_dev,
            inode=metadata.st_ino,
            mode=metadata.st_mode,
            size=metadata.st_size,
            modified_ns=metadata.st_mtime_ns,
        )


@dataclass(frozen=True)
class _FileSnapshot:
    relative_path: Path
    identity: _PathIdentity
    sha256: str | None = None


@dataclass(frozen=True)
class _Move:
    destination: Path
    source: Path
    identity: _PathIdentity


def migrate_legacy_workspace(root: Path, *, confirm: bool) -> BrandMigrationResult:
    """Move legacy configuration/state into the canonical namespace safely.

    The operation is idempotent. A mixed legacy/current workspace is rejected,
    and a complete local ZIP backup is created before either source path moves.
    Every path component is checked without following symlinks before any read,
    backup or namespace change.
    """

    root = root.resolve(strict=True)
    _require_identity(root, kind="directory", label="Workspace root")
    legacy_config = root / _LEGACY_CONFIG
    current_config = root / _CURRENT_CONFIG
    legacy_state = root / _LEGACY_STATE
    current_state = root / _CURRENT_STATE

    config_directory = _optional_lstat(root / "config")
    if config_directory is not None:
        _require_identity(root / "config", kind="directory", label="Configuration directory")

    legacy_config_identity = _optional_typed_identity(
        legacy_config, kind="file", label="Legacy configuration"
    )
    current_config_identity = _optional_typed_identity(
        current_config, kind="file", label="Current configuration"
    )
    legacy_state_identity = _optional_typed_identity(
        legacy_state, kind="directory", label="Legacy state"
    )
    current_state_identity = _optional_typed_identity(
        current_state, kind="directory", label="Current state"
    )

    legacy_exists = legacy_config_identity is not None or legacy_state_identity is not None
    if not legacy_exists:
        return BrandMigrationResult(False, None, ())
    if (legacy_config_identity is not None and current_config_identity is not None) or (
        legacy_state_identity is not None and current_state_identity is not None
    ):
        raise BrandMigrationError(
            "Legacy and current workspace paths both exist; no files were changed"
        )
    if not confirm:
        raise BrandMigrationError("Brand migration requires explicit confirmation")

    backup_files: list[_FileSnapshot] = []
    metadata_files: list[Path] = []
    if legacy_config_identity is not None:
        backup_files.append(_FileSnapshot(_LEGACY_CONFIG, legacy_config_identity))
    if legacy_state_identity is not None:
        state_files = _snapshot_tree(root, _LEGACY_STATE, legacy_state_identity)
        backup_files.extend(state_files)
        metadata_files = [
            item.relative_path.relative_to(_LEGACY_STATE)
            for item in state_files
            if "graph" not in item.relative_path.parts
            and item.relative_path.name in _METADATA_NAMES
        ]

    # Recheck every source immediately before creating any backup artifact.
    for item in backup_files:
        _verify_identity(root / item.relative_path, item.identity, kind="file")
    backup, captured_backup_files = _create_backup(root, tuple(backup_files))

    # The backup is the rollback boundary. Prove that every source still has the
    # exact bytes and topology captured in it immediately before namespace moves.
    for item in captured_backup_files:
        _verify_file_snapshot(root, item)
    if legacy_state_identity is not None:
        _verify_tree_snapshot(
            root,
            _LEGACY_STATE,
            legacy_state_identity,
            tuple(
                item
                for item in captured_backup_files
                if _LEGACY_STATE in item.relative_path.parents
            ),
        )

    moved: list[_Move] = []
    originals: dict[Path, bytes] = {}
    try:
        if legacy_config_identity is not None:
            _move_verified(
                root,
                legacy_config,
                current_config,
                legacy_config_identity,
                moved=moved,
            )
        if legacy_state_identity is not None:
            _move_verified(
                root,
                legacy_state,
                current_state,
                legacy_state_identity,
                moved=moved,
            )
            for relative in metadata_files:
                metadata = current_state / relative
                before = _read_regular(root, metadata)
                after = before.replace(b"agentos", b"raytsystem").replace(b"AgentOS", b"raytsystem")
                if after != before:
                    json.loads(after)
                    originals[relative] = before
                    write_bytes_atomic(metadata, after)
                    _read_regular(root, metadata)
    except BaseException as error:
        rollback_errors = _rollback_namespace(root, moved)
        rollback_errors.extend(_restore_metadata(root, legacy_state, current_state, originals))
        suffix = ""
        if rollback_errors:
            suffix = "; automatic rollback was incomplete and requires manual recovery"
        raise BrandMigrationError(
            f"Brand migration failed; the pre-migration backup is {backup}{suffix}"
        ) from error

    changed = tuple(str(item.destination.relative_to(root)) for item in moved)
    return BrandMigrationResult(True, str(backup.relative_to(root)), changed)


def _optional_lstat(path: Path) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None


def _optional_typed_identity(path: Path, *, kind: str, label: str) -> _PathIdentity | None:
    if _optional_lstat(path) is None:
        return None
    return _require_identity(path, kind=kind, label=label)


def _require_identity(path: Path, *, kind: str, label: str) -> _PathIdentity:
    try:
        metadata = os.lstat(path)
    except OSError as error:
        raise BrandMigrationError(f"{label} is unavailable") from error
    if stat.S_ISLNK(metadata.st_mode):
        raise BrandMigrationError(f"{label} must not be a symlink")
    if kind == "file":
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise BrandMigrationError(f"{label} is not a single-link regular file")
    elif kind == "directory":
        if not stat.S_ISDIR(metadata.st_mode):
            raise BrandMigrationError(f"{label} is not a real directory")
    else:  # pragma: no cover - internal invariant
        raise ValueError(f"Unknown path kind: {kind}")
    return _PathIdentity.from_stat(metadata)


def _verify_identity(path: Path, expected: _PathIdentity, *, kind: str) -> None:
    current = _require_identity(path, kind=kind, label="Migration path")
    stable = (current.device, current.inode, current.mode)
    expected_stable = (expected.device, expected.inode, expected.mode)
    if stable != expected_stable:
        raise BrandMigrationError("Migration path changed during the operation")
    if kind == "file" and (
        current.size != expected.size or current.modified_ns != expected.modified_ns
    ):
        raise BrandMigrationError("Migration file changed during the operation")


def _snapshot_tree(
    root: Path,
    relative_state: Path,
    state_identity: _PathIdentity,
) -> list[_FileSnapshot]:
    state = root / relative_state
    _verify_identity(state, state_identity, kind="directory")
    files: list[_FileSnapshot] = []
    for directory, directory_names, file_names in os.walk(state, followlinks=False):
        directory_path = Path(directory)
        _require_identity(directory_path, kind="directory", label="Legacy state directory")
        for name in sorted(directory_names):
            _require_identity(
                directory_path / name,
                kind="directory",
                label="Legacy state directory",
            )
        for name in sorted(file_names):
            path = directory_path / name
            identity = _require_identity(path, kind="file", label="Legacy state file")
            files.append(_FileSnapshot(path.relative_to(root), identity))
    _verify_identity(state, state_identity, kind="directory")
    return sorted(files, key=lambda item: item.relative_path.as_posix())


def _create_backup(
    root: Path, files: tuple[_FileSnapshot, ...]
) -> tuple[Path, tuple[_FileSnapshot, ...]]:
    backup_dir = root / "ops" / "backups"
    try:
        ensure_safe_directory(backup_dir, mode=0o700)
    except (OSError, RuntimeError) as error:
        raise BrandMigrationError("Backup directory is unsafe") from error
    backup_path = _open_relative_directory(root, Path("ops") / "backups")
    backup_directory_identity = _PathIdentity.from_stat(os.stat(backup_path))
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    name = f"pre-raytsystem-brand-{timestamp}.zip"
    target = backup_path / name
    file_fd: int | None = None
    captured: list[_FileSnapshot] = []
    created_identity: _PathIdentity | None = None
    try:
        flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        flags |= O_BINARY
        # ponytail: O_NOFOLLOW is POSIX-only; on Windows lexists+lstat rejects
        # symlinked leaf, leaving the O_CREAT path to create the regular file.
        if IS_WINDOWS:
            existing = _optional_lstat(target)
            if existing is not None and stat.S_ISLNK(existing.st_mode):
                raise BrandMigrationError("Backup artifact target is a symlink")
        file_fd = os.open(target, flags, 0o600)
        with os.fdopen(file_fd, "w+b", closefd=True) as handle:
            file_fd = None
            with zipfile.ZipFile(handle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for item in files:
                    path = root / item.relative_path
                    _verify_identity(path, item.identity, kind="file")
                    data = _read_regular(root, path, expected=item.identity)
                    archive.writestr(item.relative_path.as_posix(), data)
                    _verify_identity(path, item.identity, kind="file")
                    captured.append(
                        _FileSnapshot(
                            relative_path=item.relative_path,
                            identity=item.identity,
                            sha256=hashlib.sha256(data).hexdigest(),
                        )
                    )
            handle.flush()
            os.fsync(handle.fileno())
            created_identity = _PathIdentity.from_stat(os.fstat(handle.fileno()))
        created = os.lstat(target)
        if not stat.S_ISREG(created.st_mode) or created.st_nlink != 1:
            raise BrandMigrationError("Backup artifact is unsafe")
        if created_identity is None or (
            created.st_dev,
            created.st_ino,
            created.st_size,
        ) != (
            created_identity.device,
            created_identity.inode,
            created_identity.size,
        ):
            raise BrandMigrationError("Backup artifact changed after creation")
        fsync_directory(backup_path)
    except (OSError, PathPolicyError, zipfile.BadZipFile) as error:
        raise BrandMigrationError("Brand migration backup failed") from error
    finally:
        if file_fd is not None:
            os.close(file_fd)
    verification_path = _open_relative_directory(root, Path("ops") / "backups")
    try:
        verified_directory = _PathIdentity.from_stat(os.stat(verification_path))
        if (verified_directory.device, verified_directory.inode) != (
            backup_directory_identity.device,
            backup_directory_identity.inode,
        ):
            raise BrandMigrationError("Backup directory changed during creation")
        verified_file = os.lstat(target)
        if created_identity is None or (verified_file.st_dev, verified_file.st_ino) != (
            created_identity.device,
            created_identity.inode,
        ):
            raise BrandMigrationError("Backup path no longer identifies the created archive")
    except OSError as error:
        raise BrandMigrationError("Backup verification failed") from error
    return backup_dir / name, tuple(captured)


def _open_relative_directory(root: Path, relative: Path) -> Path:
    """Resolve ``root / relative`` to a Path via a symlink-rejecting walk."""

    try:
        return descend_directory(root, relative.parts)
    except OSError as error:
        raise BrandMigrationError("Directory component is missing or unsafe") from error


def _read_regular(
    root: Path,
    path: Path,
    *,
    expected: _PathIdentity | None = None,
) -> bytes:
    before = _require_identity(path, kind="file", label="Migration input")
    if expected is not None and (before.device, before.inode) != (
        expected.device,
        expected.inode,
    ):
        raise BrandMigrationError("Migration input changed before it was read")
    try:
        result = read_regular_file(root, path, max_bytes=max(before.size, 1))
    except PathPolicyError as error:
        raise BrandMigrationError("Migration input is unsafe") from error
    after = _require_identity(path, kind="file", label="Migration input")
    if (after.device, after.inode) != (before.device, before.inode):
        raise BrandMigrationError("Migration input changed while it was read")
    return result.data


def _verify_file_snapshot(root: Path, snapshot: _FileSnapshot) -> None:
    _verify_identity(root / snapshot.relative_path, snapshot.identity, kind="file")
    data = _read_regular(root, root / snapshot.relative_path, expected=snapshot.identity)
    if snapshot.sha256 is None or hashlib.sha256(data).hexdigest() != snapshot.sha256:
        raise BrandMigrationError("Migration input differs from the verified backup")


def _verify_tree_snapshot(
    root: Path,
    relative_state: Path,
    state_identity: _PathIdentity,
    expected_files: tuple[_FileSnapshot, ...],
) -> None:
    current = _snapshot_tree(root, relative_state, state_identity)
    expected_by_path = {item.relative_path: item for item in expected_files}
    if [item.relative_path for item in current] != sorted(expected_by_path):
        raise BrandMigrationError("Legacy state tree changed after backup")
    for item in current:
        expected = expected_by_path[item.relative_path]
        _verify_identity(root / item.relative_path, expected.identity, kind="file")
        _verify_file_snapshot(root, expected)


def _move_verified(
    root: Path,
    source: Path,
    destination: Path,
    identity: _PathIdentity,
    *,
    moved: list[_Move],
) -> None:
    source_relative = source.relative_to(root)
    destination_relative = destination.relative_to(root)
    if source_relative.parent != destination_relative.parent:
        raise BrandMigrationError("Migration move crossed a managed parent boundary")
    kind = "file" if stat.S_ISREG(identity.mode) else "directory"
    parent_path = _open_relative_directory(root, source_relative.parent)
    _verify_identity_at(parent_path, source_relative.name, identity, kind=kind)
    _rename_no_replace_at(
        parent_path,
        source_relative.name,
        parent_path,
        destination_relative.name,
    )
    record = _Move(destination=destination, source=source, identity=identity)
    moved.append(record)
    _verify_identity_at(parent_path, destination_relative.name, identity, kind=kind)


def _rollback_namespace(root: Path, moved: list[_Move]) -> list[BaseException]:
    errors: list[BaseException] = []
    for item in reversed(moved):
        try:
            destination_relative = item.destination.relative_to(root)
            source_relative = item.source.relative_to(root)
            if destination_relative.parent != source_relative.parent:
                raise BrandMigrationError("Rollback crossed a managed parent boundary")
            kind = "file" if stat.S_ISREG(item.identity.mode) else "directory"
            parent_path = _open_relative_directory(root, destination_relative.parent)
            _verify_identity_at(
                parent_path,
                destination_relative.name,
                item.identity,
                kind=kind,
            )
            _rename_no_replace_at(
                parent_path,
                destination_relative.name,
                parent_path,
                source_relative.name,
            )
            _verify_identity_at(
                parent_path,
                source_relative.name,
                item.identity,
                kind=kind,
            )
        except BaseException as error:
            errors.append(error)
    return errors


def _verify_identity_at(
    directory: Path,
    name: str,
    expected: _PathIdentity,
    *,
    kind: str,
) -> None:
    try:
        metadata = lstat_under(directory, name)
    except OSError as error:
        raise BrandMigrationError("Migration namespace entry is unavailable") from error
    if stat.S_ISLNK(metadata.st_mode):
        raise BrandMigrationError("Migration namespace entry became a symlink")
    current = _PathIdentity.from_stat(metadata)
    stable = (current.device, current.inode, current.mode)
    expected_stable = (expected.device, expected.inode, expected.mode)
    if stable != expected_stable:
        raise BrandMigrationError("Migration namespace identity changed")
    if kind == "file" and (
        current.size != expected.size or current.modified_ns != expected.modified_ns
    ):
        raise BrandMigrationError("Migration namespace file changed")
    if kind == "file" and (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1):
        raise BrandMigrationError("Migration namespace file is unsafe")
    if kind == "directory" and not stat.S_ISDIR(metadata.st_mode):
        raise BrandMigrationError("Migration namespace directory is unsafe")


def _rename_no_replace_at(
    source_parent: Path,
    source_name: str,
    destination_parent: Path,
    destination_name: str,
) -> None:
    """Rename one namespace entry without ever replacing the destination."""

    if IS_WINDOWS:
        try:
            rename_under(
                source_parent, source_name, destination_parent, destination_name
            )
        except FileExistsError as error:
            raise BrandMigrationError(
                "Migration destination already exists"
            ) from error
        except OSError as error:
            raise BrandMigrationError(
                "Migration namespace move failed"
            ) from error
        return

    source_bytes = os.fsencode(source_name)
    destination_bytes = os.fsencode(destination_name)
    parent_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    source_fd = os.open(source_parent, parent_flags)
    try:
        destination_fd = os.open(destination_parent, parent_flags)
        try:
            if sys.platform == "darwin":
                libc = ctypes.CDLL(None, use_errno=True)
                rename = getattr(libc, "renameatx_np", None)
                if rename is None:  # pragma: no cover - supported macOS API
                    raise BrandMigrationError("No-replace rename is unavailable")
                rename.argtypes = [
                    ctypes.c_int,
                    ctypes.c_char_p,
                    ctypes.c_int,
                    ctypes.c_char_p,
                    ctypes.c_uint,
                ]
                rename.restype = ctypes.c_int
                result = rename(
                    source_fd,
                    source_bytes,
                    destination_fd,
                    destination_bytes,
                    0x00000004,  # RENAME_EXCL
                )
            elif sys.platform.startswith("linux"):
                libc = ctypes.CDLL(None, use_errno=True)
                rename = getattr(libc, "renameat2", None)
                if rename is None:  # pragma: no cover - old libc fails closed
                    raise BrandMigrationError("No-replace rename is unavailable")
                rename.argtypes = [
                    ctypes.c_int,
                    ctypes.c_char_p,
                    ctypes.c_int,
                    ctypes.c_char_p,
                    ctypes.c_uint,
                ]
                rename.restype = ctypes.c_int
                result = rename(
                    source_fd,
                    source_bytes,
                    destination_fd,
                    destination_bytes,
                    1,  # RENAME_NOREPLACE
                )
            else:  # pragma: no cover - unsupported platforms fail closed
                raise BrandMigrationError(
                    "No-replace rename is unavailable on this platform"
                )
        finally:
            os.close(destination_fd)
    finally:
        os.close(source_fd)

    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise BrandMigrationError("Migration destination appeared concurrently")
    raise BrandMigrationError("Migration namespace move failed") from OSError(
        error_number,
        os.strerror(error_number),
    )


def _restore_metadata(
    root: Path,
    legacy_state: Path,
    current_state: Path,
    originals: dict[Path, bytes],
) -> list[BaseException]:
    errors: list[BaseException] = []
    state = legacy_state if _optional_lstat(legacy_state) is not None else current_state
    for relative, before in originals.items():
        try:
            _require_identity(state, kind="directory", label="Rollback state")
            target = state / relative
            _require_identity(target, kind="file", label="Rollback metadata")
            write_bytes_atomic(target, before)
            _read_regular(root, target)
        except BaseException as error:
            errors.append(error)
    return errors
