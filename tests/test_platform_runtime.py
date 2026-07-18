"""Smoke tests for ``platform_runtime`` path-based helpers.

These exercise the dual-mode invariants the codebase relies on: TOCTOU
guards (symlink rejection), atomic-rename semantics (``rename_under``
must raise ``FileExistsError``), and round-trip ``mkdir_under`` +
``lstat_under`` + ``unlink_under``. They are intentionally small; full
POSIX-vs-Windows parity is covered by the existing ``test_skill_authoring``,
``test_storage``, and ``test_path_policy`` suites.
"""

from __future__ import annotations

import os
import stat
import sys

import pytest

from raytsystem import platform_runtime as pr

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" and os.environ.get("RAYTSYSTEM_PLATFORM_ASSUME") == "posix",
    reason="forced posix branch on a Windows host cannot open dir fds",
)


def _make_dir(tmp_path) -> object:
    leaf = tmp_path / "leaf"
    leaf.mkdir()
    return leaf


def test_descend_directory_rejects_missing_component(tmp_path) -> None:
    with pytest.raises(OSError):
        pr.descend_directory(tmp_path, ["does-not-exist"])


def test_descend_directory_rejects_symlinked_parent(tmp_path) -> None:
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks not supported")
    try:
        os.symlink(tmp_path / "real", tmp_path / "link")
    except OSError:
        pytest.skip("symlinks require privilege on this host")
    (tmp_path / "real").mkdir()
    with pytest.raises(OSError):
        pr.descend_directory(tmp_path, ["link"])


def test_mkdir_lstat_unlink_round_trip(tmp_path) -> None:
    leaf = _make_dir(tmp_path)
    pr.mkdir_under(leaf, "child", 0o755)
    meta = pr.lstat_under(leaf, "child")
    assert stat.S_ISDIR(meta.st_mode)
    pr.rmdir_under(leaf, "child")


def test_open_under_rejects_symlink_leaf(tmp_path) -> None:
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks not supported")
    leaf = _make_dir(tmp_path)
    target = leaf / "real.txt"
    target.write_bytes(b"hello")
    try:
        os.symlink(target, leaf / "link.txt")
    except OSError:
        pytest.skip("symlinks require privilege on this host")
    with pytest.raises(OSError):
        pr.open_under(leaf, "link.txt", os.O_WRONLY, 0o644)


def test_open_under_creates_new_file_with_o_creat(tmp_path) -> None:
    leaf = _make_dir(tmp_path)
    fd = pr.open_under(
        leaf, "new.txt", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644
    )
    os.write(fd, b"data")
    os.close(fd)
    meta = pr.lstat_under(leaf, "new.txt")
    assert stat.S_ISREG(meta.st_mode)
    pr.unlink_under(leaf, "new.txt")


def test_rename_under_raises_on_existing_destination(tmp_path) -> None:
    leaf = _make_dir(tmp_path)
    (leaf / "src.txt").write_bytes(b"a")
    (leaf / "dst.txt").write_bytes(b"b")
    with pytest.raises(FileExistsError):
        pr.rename_under(leaf, "src.txt", leaf, "dst.txt")


def test_replace_under_swaps_existing_destination(tmp_path) -> None:
    leaf = _make_dir(tmp_path)
    (leaf / "src.txt").write_bytes(b"new")
    (leaf / "dst.txt").write_bytes(b"old")
    pr.replace_under(leaf, "src.txt", leaf, "dst.txt")
    assert (leaf / "dst.txt").read_bytes() == b"new"
    assert not (leaf / "src.txt").exists()


def test_hardlink_under_creates_second_link(tmp_path) -> None:
    leaf = _make_dir(tmp_path)
    (leaf / "src.txt").write_bytes(b"data")
    pr.hardlink_under(leaf, "src.txt", leaf, "link.txt")
    assert (leaf / "link.txt").read_bytes() == b"data"


def test_kill_process_tree_terminates_subprocess(tmp_path) -> None:
    import subprocess
    import sys as _sys

    proc = subprocess.Popen([_sys.executable, "-c", "import time; time.sleep(30)"])
    code = pr.kill_process_tree(proc, timeout=5.0)
    assert code is not None


def test_self_check_passes() -> None:
    pr._self_check()
