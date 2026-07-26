# Roadmap

raytsystem is pre-1.0. This roadmap describes direction, not a delivery promise.

## Before the first public release

- Complete public secret/path/corpus hygiene and a clean-clone install test.
- Publish raytsystem Documentation through GitHub Pages.
- Establish green CI, CodeQL, dependency review, release artifacts, checksums, and provenance.
- Validate the reviewed synthetic screenshot flow on Linux CI and macOS locally.

## Toward 1.0

- Qualify Documents editing, conflict recovery, backup/export/restore, and upgrade paths.
- Expand deterministic end-to-end coverage for tasks, runs, approvals, and graph freshness.
- Define supported runtime adapters without weakening the default-deny external boundary.
- Add migration and compatibility guarantees for stable public schemas and CLI commands.
- Complete a consented pilot with non-synthetic data outside the public repository.

## Windows native port (feature/windows)

**Статус: Phase 4 + graph fix — завершено.** Полная webapp + CLI работает на Windows 10/11
без WSL. Code graph собирается и кэшируется.

Актуальные задачи — в `TODO.md`. История изменений — в `CHANGELOG.md`.

Что сделано (кратко):
- `platform_runtime.py` — централизация OS-расхождений
- CRLF, SQLite handle leaks, WAL sidecar fallback — починены
- Code graph rebuild работает (env fix + POSIX path normalization)
- Launchers `.bat`/`.ps1`, WINDOWS.md, USER_INSTRUCTIONS.md
- win32 skips для POSIX-only тестов

Что осталось (подробности в `TODO.md`):
- 4 symlink-теста добить skipif win32
- Skill CRUD (create + delete)
- Архитектурный рефакторинг (дедупликация SQLite/CRLF паттернов)
- Агенты: enable/disable toggle, run из UI

Requests belong in GitHub issues. Planned work can change after security
review or user validation.
