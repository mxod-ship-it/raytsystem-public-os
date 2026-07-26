# Changelog

All notable changes to raytsystem are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses semantic versioning
for tagged pre-1.0 releases.

## [Unreleased]

### Added

- Public repository documentation, community health files, issue forms, CI, security scanning,
  GitHub Pages deployment, and tag-gated release automation.
- Reproducible synthetic product screenshots for the GitHub README and social preview.

### Changed

- Reworked the repository home page around verified capabilities, current limits, installation,
  security defaults, and the public documentation site.

## [0.1.0] - Unreleased

Initial public pre-1.0 release. The tag will be created only after a clean public clone/install test
and separate maintainer approval.

[Unreleased]: https://github.com/romarayt/raytsystem-public-os/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/romarayt/raytsystem-public-os/releases/tag/v0.1.0

---

## [windows-port] - 2026-07-26

Нативный порт на Windows 10/11 (без WSL/Sandbox). Ветка `feature/windows`.

### Added

- `platform_runtime.py` — единый модуль для всех OS-расхождений (detection, fsync, file flags, kill tree, atomic_replace).
- `raytsystem-start.bat` — double-click лаунчер (пауза перед закрытием).
- `raytsystem-start.ps1` — лаунчер с auto-elevation до Administrator.
- `WINDOWS.md` — документация: install, launch, configuration, pitfalls, verification.
- `USER_INSTRUCTIONS.md` — руководство пользователя простым языком (RU).
- `config/windows.yaml` и `config/windows.local.yaml` — per-machine overrides.

### Changed

- Skill authoring, brand migration, documents service портированы с POSIX `dir_fd=` на path-based `lstat`-walk (Phases 3a–3f).
- `_ScopedConnection` context manager в `search.py` и `documents/index.py` (close + gc.collect на exit).
- `.gitignore` — добавлены git merge/rebase артефакты (`*.orig`, `*.rej`, `REBASE_HEAD`, и т.д.).
- win32 skips для POSIX-only symlink/hardlink тестов (10 шт) и ffprobe тестов (2 шт).

### Fixed

- **Code graph на Windows** — `extract_file_isolated` и `PdfExtractor.extract` строили env dict без `SYSTEMROOT` → worker subprocess вис. Fix: `os.environ.copy()` + override. Добавлена `_normalize_file()` для POSIX-нормализации путей.
- **CRLF tolerance** — `catalog.py`, `skill_authoring.py`, `classify.py` нормализуют `\r\n` → `\n` на входе парсеров. `source_sha256` считается от сырых байтов — хеши не меняются.
- **SQLite handle leaks** — `sqlite3.Connection.close()` на Windows держит OS handle до GC. Fix: `atomic_replace()` (gc + retry + MoveFileExW fallback), `gc.collect()` после close в rebuild путях, `_ScopedConnection`.
- **WAL sidecar fallback** — `execution/store.py` `open_for_read` падает с `immutable=1` на live `mode=ro` когда writer оставил `-wal`/`-journal` sidecar.
- **Handbook labels** — `webapp/handbook.py` передаёт `.as_posix()` строку вместо `Path` объекта.
