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

## [windows-port.2] - 2026-07-26

### Changed

- win32 skips для ещё 4 symlink-тестов (`test_catalog`, `test_path_policy`, `test_execution_config`, `test_execution_adapters`).

## [windows-port.3] - 2026-07-26 20:15

### Changed

- Temp-директория pytest уведена с `C:\Users\...\Temp\pytest-of-*` (заблокирован) в `D:\TEMP\pytest`.
  Root `conftest.py` форсирует `basetemp` на `win32` автоматически; в `.gitignore` добавлен `/conftest.py` (локальный override, не ship'ится).
- `AGENTS.md` — правило temp-директории задокументировано в секции Commands.

## [windows-port.4] - 2026-07-26 21:03

### Диагностика — полный прогон `pytest` на win32

`RAYTSYSTEM_PLATFORM_DISABLE_FSYNC=1`, basetemp `D:\TEMP\pytest`, 22 мин.

- **698 passed, 32 skipped, 19 failed.**
- 32 skipped — POSIX-only symlink/hardlink/fork/ffprobe/POSIX-mode (включая 4 skip'а из [windows-port.2]).
- **19 failed** — почти все symlink-rejection тесты: NTFS не даёт создавать symlink без привилегий → `OSError` ДО того как система успевает его проверить. Тесты валидны как инвариант на POSIX, но не воспроизводимы на win32 без Developer Mode/elevation.
  - `test_brand_migration` (6), `test_documents` (1), `test_m2_lint_save` (1), `test_m2_search_query` (2),
    `test_platform_backup` (1), `test_skill_authoring` (1), `test_tasking` (1), `test_toolhub_video` (2),
    `test_write_policy` (2), `test_execution_workspace` (1).
- **1 не-symlink failure** — `test_m3_routing::test_agents_and_work_are_small_exact_surface_routers` (разобрать отдельно; не связан с symlink-эскейпом).
- Вывод: кодовая регрессия отсутствует; все fail'ы — win32-специфика symlink-создания + один роторный тест на разбор.

## [windows-port.5] - 2026-07-27

### Changed

- **Skip 18 symlink-rejection тестов на win32** — добавлены маркеры `@pytest.mark.skipif(sys.platform == "win32", reason="symlink/hardlink: POSIX-only")` в 7 тест-файлах. NTFS без Developer Mode/elevation не даёт создавать symlink → `OSError [WinError 1314]` летит ДО того, как система успевает проверить reject-инвариант, поэтому тесты падают по окружению, а не по коду. На POSIX (CI Linux) тесты продолжают гоняться.
  - `test_brand_migration.py` — 5 функций (6 кейсов с параметризацией `["ops", "backups"]`): legacy_state, config_component, backup_components, nested_state, parent_swap.
  - `test_platform_backup.py` — `test_private_backup_rejects_symlinked_platform_store_before_publication`.
  - `test_documents.py` — `test_symlink_is_never_indexed`.
  - `test_execution_workspace.py` — `test_workspace_rejects_traversal_and_symlinked_managed_root`.
  - `test_m2_lint_save.py` — `test_save_rejects_symlinked_output_parent_without_external_write`.
  - `test_m2_search_query.py` — 2 параметризованные функции (`test_index_rebuild_rejects_unsafe_link_targets...`, `test_projection_rejects_unsafe_graph_target...`), skipif ВЫШЕ `@parametrize`.
  - `test_skill_authoring.py` — `test_source_symlink_and_hardlink_are_rejected`.
  - `test_tasking.py` — `test_symlinked_writer_lock_is_rejected` (добавлен второй skipif сверху к существующему `not hasattr(os, "symlink")`, который на win32 проходит, но вызов падает).
  - `test_toolhub_video.py` — 2: `test_stage_symlink_is_rejected_before_writing`, `test_staging_root_replacement_is_rejected`.
  - `test_write_policy.py` — 2: `test_symlinked_normalized_root_cannot_escape_workspace`, `test_symlinked_ops_root_cannot_redirect_control_db`.
- В 5 тест-файлах добавлен `import sys` (отсутствовал): `test_brand_migration.py`, `test_platform_backup.py`, `test_documents.py`, `test_tasking.py`, `test_write_policy.py`.

### Верификация

Полный прогон `RAYTSYSTEM_PLATFORM_DISABLE_FSYNC=1 uv run pytest --tb=line -p no:cacheprovider -q` (basetemp `D:\TEMP\pytest`, ~22 мин):

- **696 passed, 52 skipped, 1 failed.**
- 52 skipped — POSIX-only symlink/hardlink/fork/ffprobe/POSIX-mode/git-index-stat-cache + archived M5a + opt-in benchmark.
- **1 failed** — `test_m3_routing::test_agents_and_work_are_small_exact_surface_routers`. Единственный не-symlink fail; не связан с win32 symlink-эскейпом, вынесен в `TODO.md` на отдельный разбор.

### Итог windows-port

Все win32-специфичные symlink-rejection fail'ы закрыты. Локальный прогон на native Windows 10 теперь сигнальный: красным остаётся только то, что реально требует внимания (регрессия или не-win32-специфичная поломка).

## [windows-port.6] - 2026-07-27

### Changed

- **Лимиты маршрутизаторов в `test_m3_routing` подняты ×2** по решению пользователя: `AGENTS.md` 65→130, `WORK.md` 25→50. `SKILL.md` (150) не тронут. Причина: инвариант small-surface-router ослаблен для файлов настроек/структуры — давить их размер не нужно.
- **AGENTS.md сжат** (вариант A): секция `## Project tracking files` (таблица + правила, ~20 строк) объединена с `## Documentation` и сжата в ~7 строк. AGENTS.md: 73 → 62 строки. Содержимое правил (`append with date`, `[x]` для done, обновление шапки TODO) сохранено в краткой форме.

### Fixed

- `test_m3_routing::test_agents_and_work_are_small_exact_surface_routers` — единственный не-symlink fail прогона `[windows-port.4]`. Причина: превышение лимита строк AGENTS.md. После правок — 4 passed в `test_m3_routing.py`.

### Верификация

`uv run pytest tests/test_m3_routing.py -v` → **4 passed in 0.96s**. AGENTS.md = 62 строки (лимит 130), WORK.md = 17 строк (лимит 50).
