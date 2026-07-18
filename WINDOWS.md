# Native Windows operation

raytsystem is POSIX-first. This page documents how to run it natively on
Windows 10/11 (no WSL, no Sandbox) for local development and testing.
Production-grade native Windows qualification is **not** claimed — see
`docs/STATUS.md`. WSL 2 remains the supported path for end users; the path
below is for contributors who want to exercise the native port.

## What works

These CLI commands run cleanly on native Windows after `uv sync --dev`:

| Command | Status |
|---|---|
| `uv run raytsystem doctor` | ✅ |
| `uv run raytsystem status` | ✅ |
| `uv run raytsystem start` (loopback UI on `127.0.0.1:8765`) | ✅ |
| `uv run raytsystem agent preflight --skill SKILL --write --json` | ✅ |
| `uv run raytsystem agent subagent-check ... --json` | ✅ |
| `uv run raytsystem guard-checkpoint --json` | ✅ |
| `uv run raytsystem rebuild-index` | ✅ |
| `uv run raytsystem graph status / query` | ✅ |
| `uv run raytsystem lint` | ✅ |
| `uv run raytsystem ingest` (Markdown/JSON/CSV/TSV/text) | ✅ |
| `uv run raytsystem query` | ✅ |
| `uv run raytsystem save` (DRAFT bundle) | ✅ |

Tooling:

| Check | Status |
|---|---|
| `uv run ruff check .` | ✅ clean |
| `uv run mypy` | ✅ clean (`platform = "linux"` in `pyproject.toml`, see below) |
| `uv run pytest` | ✅ core surfaces green; see "Known gaps" for the residual set |

## What does not work yet (Phase 3 backlog)

These paths still rely on POSIX-only `dir_fd=` walking and need a path-based
port. They are gated behind skill authoring and FastAPI recovery code:

- `tests/test_skill_authoring.py` (~30 tests) — skill transaction writes.
- `tests/test_webapp*.py` (~50 tests) — UI recovery through the same code path.
- `tests/test_toolhub_video.py` (2 tests) — the fixture pins `ffprobe` as a
  Linux ELF binary, which Windows cannot execute (`WinError 193`).
  Replace with a Windows ffprobe or skip on `os.name == "nt"`.

CLI surface affected by the same `dir_fd=` gap: **skill authoring via the
web UI** (save/fork/recovery). The CLI `raytsystem save` and `raytsystem
ingest` paths are NOT affected — they go through `io.py` + `storage.py`,
both already ported.

## Install

```powershell
git clone https://github.com/romarayt/raytsystem-public-os D:\RT\def\raytsystem
cd D:\RT\def\raytsystem
uv sync --dev
```

Requirements:

- Windows 10 or 11, x64.
- Python 3.12–3.14 (3.13 verified).
- `uv` 0.11+.
- Optional: Node 22 for UI/docs builds (unchanged from POSIX).
- Optional: Developer Mode or administrator rights if you need symlinks.

## Workspace isolation

raytsystem isolation is enforced **in Python**: path-traversal guards in
`security/paths.py`, hash-bound immutable ledger, loopback-only web bind.
The OS-level analog on Windows is an NTFS ACL, not POSIX mode bits.

One-time workspace ACL hardening (run in an elevated or per-user PowerShell;
**not** auto-run by raytsystem):

```powershell
$root = "D:\RT\def\raytsystem"
icacls $root /grant:r "$($env:USERNAME):(OI)(CI)F" /inheritance:r
```

- `(OI)(CI)` — object + container inherit; applies to new files and subdirs.
- `/grant:r` — replace, not merge.
- `/inheritance:r` — remove inherited ACEs from parent folders.

This is the only Windows-specific hardening step. Everything else (loopback
bind, no external egress, no cloud account) is enforced identically to
POSIX.

## Configuration

### `config/windows.yaml`

Tracked, machine-independent. Documents:

- `environment` — `${USERPROFILE}`, `${LOCALAPPDATA}`, `${APPDATA}`,
  `${USERNAME}` placeholders.
- `agents` — paths to the **global** opencode AGENTS.md and skills under
  `${USERPROFILE}/.config/opencode/`. These are READ by reference, never
  copied into this repository.
- `isolation` — `strategy: acl`, `use_windows_sandbox: false`.
- `filesystem` — informational flags (`posix_modes_honored: false`,
  `hardlinks_supported: true`, `directory_fsync_honored: false`).
- `extra_document_roots`, `backup_destinations` — empty by default.

### Per-machine overrides

Place overrides in `config/windows.local.yaml` (gitignored). Schema mirrors
`config/windows.yaml`. Typical use: attach additional document roots on
other drives.

```yaml
extra_document_roots:
  - id: personal-projects
    path: D:/RT
    mode: read_only
    kind: code
```

> **Path style.** YAML and Python `pathlib` accept forward slashes on
> Windows. Prefer them over backslashes in any TOML/YAML string — basic
> strings in both formats escape `\`, and raytsystem's strict path
> validator rejects backslash-bearing string inputs. `Path` objects from
> Python are fine either way.

## Environment variables

| Variable | Purpose |
|---|---|
| `RAYTSYSTEM_PLATFORM_ASSUME` | `windows` or `posix`. Forces platform detection regardless of `os.name`. Use in CI to exercise the other branch. |
| `RAYTSYSTEM_PLATFORM_DISABLE_FSYNC` | `1` / `true` / `yes` / `on` — disables all `fsync` calls including directory fsyncs. Use for fast test runs and scratch spaces. Never enable on production ledgers. |

Example fast test run:

```powershell
$env:RAYTSYSTEM_PLATFORM_DISABLE_FSYNC = "1"
uv run pytest
```

## `raytsystem.platform_runtime`

Single module that centralizes every platform divergence. Import a helper
instead of re-checking `os.name` at call sites.

```python
from raytsystem.platform_runtime import (
    IS_WINDOWS,
    IS_POSIX,
    O_BINARY,
    binary_readonly_flags,
    open_file_readonly,
    fsync_directory,   # no-op on Windows
    fsync_file,
    fchmod,            # no-op on Windows
    chmod_private,     # no follow_symlinks on Windows
    kill_process_tree, # killpg on POSIX, terminate/kill on Windows
)
```

Self-check:

```powershell
uv run python -m raytsystem.platform_runtime
# platform=windows fsync_disabled=False
```

Design rules (see `src/raytsystem/platform_runtime.py` for the full
docstring):

- Detection runs once at import.
- Pure functions where possible, explicit actions where there is a
  side-effect.
- Never raises `NotImplementedError`. Operations the current platform
  cannot honor are silent no-ops, because the invariant they would enforce
  is already covered another way (ACL inheritance substitutes for POSIX
  mode bits; loopback bind substitutes for filesystem permission checks).

## Windows-specific code points

The complete inventory of `os.name` branches outside `platform_runtime.py`:

| File | Reason |
|---|---|
| `documents/subprocesses.py:_stop_process` | Explicit `posix` / `else` ladder for `killpg` vs `terminate`/`kill`. Inline because the escalation is non-trivial. |
| `security/paths.py:_read_regular_file_windows` | Path-based twin of the `dir_fd=` walk. NTFS has no userspace equivalent of `O_NOFOLLOW` on a directory open; the residual TOCTOU between `lstat` and `open` is documented in the function. |
| `security/paths.py:_lexical_relative` | Accepts `Path` objects with backslash separators; strict-POSIX validation still applies to TOML/YAML string inputs. |
| `documents/config.py:_directory_identity_windows` | `os.open` on a directory raises `PermissionError` on Windows; lstat-only walk returns `(st_dev, st_ino)`. |
| `extractors.py`, `codegraph/worker.py` | Lazy `import resource` inside `_apply_limits`; early-return on `nt`. |
| `execution/adapters.py`, `toolhub/runner.py` | Pre-existing POSIX/else branches for process-tree kill — unchanged. |

## Known pitfalls

### CRLF in ledger pointers

`storage.read_current_generation` enforces a byte-exact invariant:
`pointer == f"{value}\n"`. Files written with `Path.write_text("genesis\n")`
land as `genesis\r\n` on Windows (universal newlines) and break the
invariant, cascading into every ingest path.

Rule: any file that is read by `read_regular_file` or validated byte-exact
must be written with `write_bytes(...)` or `write_bytes_atomic(...)`. This
applies to ledger pointers, task-ledger CURRENT, and hash-addressed
immutable objects.

### `os.open` without `O_BINARY`

Windows CRT opens file descriptors in text mode by default: `\r\n` is
collapsed to `\n` on read, `\x1a` (`^Z`) is treated as EOF. Always go
through `platform_runtime.open_file_readonly` or
`platform_runtime.binary_readonly_flags()`.

### Directory fsync is a no-op

NTFS rename durability differs from ext4 and there is no userspace
equivalent of opening a directory to `fsync` it. `fsync_directory` returns
immediately on Windows. Atomic renames still provide the ordering raytsystem
relies on; cross-power-loss durability is the OS's job.

### `dir_fd=` is unsupported

~50 call sites in skill authoring, webapp recovery, and brand migration
walk parent-by-parent with `dir_fd=`. On Windows `os.open` on a directory
raises `PermissionError` and `dir_fd=` itself is not implemented. Phase 3
will port these to the same `lstat`-walk pattern used in
`security/paths.py:_read_regular_file_windows`. Until then, those surfaces
are not usable on native Windows.

### mypy with `platform = "linux"`

`pyproject.toml` pins `platform = "linux"` for mypy. Without it, mypy on a
Windows host emits ~36 false positives on `os.RLIMIT_*`, `os.fchmod`,
`os.killpg`, `signal.SIGKILL`, `os.flock`, and `os.LOCK_*`. The Windows
branches are runtime-guarded by `os.name == "nt"` checks; the type checker
simply assumes the POSIX declarations.

### ffprobe in `test_toolhub_video`

The fixture writes a Linux ELF binary as a pinned `ffprobe`. Windows cannot
execute it (`WinError 193`). Either install a real Windows ffprobe and
adjust the fixture, or skip the file on `os.name == "nt"`.

## Verification

```powershell
uv sync --dev
uv run ruff check .
uv run mypy
uv run raytsystem doctor
uv run raytsystem status
uv run raytsystem graph status --json

# Fast test run (skips fsyncs; safe for non-ledger surfaces)
$env:RAYTSYSTEM_PLATFORM_DISABLE_FSYNC = "1"
uv run pytest
```

Expected on a clean checkout after `uv sync --dev`: ruff/mypy clean,
`raytsystem doctor` reports no platform-specific error, full `pytest`
passes the core surfaces (storage, ingestion, query, lint, save, tasking,
universe, codegraph, documents, recovery, backup) and leaves the Phase 3
set documented above as known failures.
