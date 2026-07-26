# TODO — задачи и планы

Последнее обновление: 2026-07-26

---

## High priority

### Windows port — завершение

- [ ] **Закоммитить накопившиеся правки** (13 файлов): graph fix, launchers, test skips, USER_INSTRUCTIONS.md, CHANGELOG.md, ROADMAP.md, TODO.md
- [ ] **Удалить `ops/skill-authoring-recovery/`** — каталог под admin ACL. Команда (от админа):
  ```powershell
  Remove-Item -Recurse -Force "D:\RT\def\raytsystem\ops\skill-authoring-recovery"
  ```
- [ ] **Добавить `skipif win32` к 4 symlink-тестам:**
  - `test_catalog.py::test_catalog_rejects_symlinked_skill_directory`
  - `test_path_policy.py::test_symlink_parent_and_final_component_are_rejected`
  - `test_execution_config.py::test_config_symlink_is_rejected`
  - `test_execution_adapters.py::test_managed_cwd_fails_closed_if_component_becomes_symlink`
- [ ] **Полный `pytest -q` после перезагрузки** — 654 ошибки были из-за залоченной temp-директории `pytest-of-mgod` (запущенное приложение), не регрессия кода

### Skill CRUD

- [ ] **Create from scratch** — `POST /api/v1/skills` + `SkillAuthoringService.create_blank()` + UI-форма с шаблоном frontmatter
- [ ] **Delete** — `DELETE /api/v1/skills/{id}` + `SkillAuthoringService.delete()` + confirm dialog в UI
- [ ] Edit/Save уже работает (`POST /api/v1/skills/{id}/save` + `SkillEditor`)

---

## Medium priority

### Архитектура (рефакторинг)

- [ ] **`rebuild_sqlite_atomic(path, builder_fn)`** — вынести повторяющийся паттерн tempfile → write → close → gc.collect → atomic_replace в один хелпер. Сейчас 3 копии: `documents/index.py`, `search.py`, `execution/store.py`
- [ ] **`_normalize_crlf(data: bytes)`** — вынести в platform_runtime. Сейчас `.replace(b"\r\n", b"\n")` в 4+ местах
- [ ] **`build_sandbox_env()`** — хелпер для worker subprocess env в platform_runtime. Сейчас 2 копии (extract.py, extractors.py)
- [ ] **Вынести `_ScopedConnection`** из search.py и documents/index.py в platform_runtime.py (дублирование)
- [ ] **Уменьшить retry count** в `atomic_replace` с 20 до 5-8 (worst case ~21s → ~5s)

### Агенты

- [ ] **Объяснить пользователю** работу агентов (он ещё не решил кого включать)
- [ ] **Enable/disable toggle** в web UI (toggle switch на странице агента)
- [ ] **"Run agent" кнопка** — trigger execution из браузера
- [ ] **Включить starter-агентов** — `enabled: true` в YAML + runtime adapter (после решения пользователя)

### OpenCode интеграция

- [ ] **`adapter_opencode`** в `config/runtime-adapters.yaml` + `OpenCodeAdapter` class в `execution/adapters.py`
- [ ] **MCP server расширение** — добавить tools `query`, `graph_query`, `graph_status` в `toolhub/mcp_server.py`
- [ ] **Синхронизация `.opencode/skills/`** из canonical skills каталога

---

## Low priority

### Рефакторинг god classes

- [ ] `IngestPipeline` (~3000 строк) → разбить на IngestPlanner / IngestExecutor / IngestPromoter
- [ ] `SkillAuthoringService` (~2400 строк) → SkillReader / SkillWriter / SkillRecovery
- [ ] `execution_views.py` (~1215 строк) → разделить data assembly / security projection / pagination

### Качество

- [ ] **Repository-паттерн для SQLite** (UnitOfWork) — абстрагировать "open connection, do work, close"
- [ ] **Тесты не дёргают приватные методы** — `_context`, `_fork_content` должны стать публичным API
- [ ] **Frontend-backend alignment** — write endpoints существуют но UI их не вызывает (assign, pause, cancel)

---

## Done (этот сеанс, незакоммичено)

- [x] Code graph fix на Windows (env + POSIX paths) — `extract.py`, `extractors.py`
- [x] Graph rebuild работает: 358 файлов, 5455 nodes, 23129 edges, ~7 мин (кэшируется)
- [x] `USER_INSTRUCTIONS.md` — руководство пользователя
- [x] `CHANGELOG.md` — дописан раздел [windows-port]
- [x] `ROADMAP.md` — актуализирован
