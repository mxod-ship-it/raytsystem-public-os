import { AlertTriangle, Plus, X } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { ErrorState, StatusPill } from "../components/StatePanel";
import { localizedCatalogLabel, statusLabel } from "../presentation";
import { useSkillCreate, useSkillCreatePreview } from "../skillHooks";
import type { SkillWriteResult } from "../types";

interface SkillCreatePanelProps {
  expectedCatalogSha256: string;
  onCancel: () => void;
  onCreated: (result: SkillWriteResult) => void;
}

const SKILL_ID_PATTERN = /^[a-z][a-z0-9_-]{1,63}$/;

function skillTemplate(skillId: string): string {
  return `---
name: ${skillId}
description: <Одно-строчное описание skill и когда вызывать>
version: 0.1.0
permissions: []
test_status: pending
---

# ${skillId}

## Что делает skill

<Короткое описание.>

## Когда использовать

<Триггеры, ключевые слова, routing-правила.>

## Шаги

1. <Первый шаг>
2. <Второй шаг>

## Inputs

- <name>: <описание, тип, обязательность>

## Outputs

- <name>: <описание>

## Permissions

<Declared filesystem/tools/network access>
`;
}

export function SkillCreatePanel({ expectedCatalogSha256, onCancel, onCreated }: SkillCreatePanelProps) {
  const previewMutation = useSkillCreatePreview();
  const createMutation = useSkillCreate();
  const [newSkillId, setNewSkillId] = useState("");
  const [content, setContent] = useState("");
  const [contentTouched, setContentTouched] = useState(false);
  const commitKey = useRef(crypto.randomUUID());

  const templateForCurrentId = useMemo(() => {
    const trimmed = newSkillId.trim();
    return trimmed && SKILL_ID_PATTERN.test(trimmed) ? skillTemplate(trimmed) : "";
  }, [newSkillId]);

  const effectiveContent = contentTouched ? content : templateForCurrentId;

  const requestPreview = () => {
    previewMutation.reset();
    createMutation.reset();
    void previewMutation.mutateAsync({
      newSkillId: newSkillId.trim(),
      content: effectiveContent,
      expectedCatalogSha256,
      idempotencyKey: crypto.randomUUID()
    }).catch(() => undefined);
  };

  const preview = previewMutation.data;
  const previewMatches = preview?.new_skill_id === newSkillId.trim() && Boolean(effectiveContent);
  const create = () => {
    if (!previewMatches) return;
    void createMutation.mutateAsync({
      newSkillId: newSkillId.trim(),
      content: effectiveContent,
      expectedCatalogSha256,
      idempotencyKey: commitKey.current
    }).then(onCreated).catch(() => undefined);
  };

  return (
    <section className="skill-create-panel panel" aria-label="Создание нового skill">
      <header>
        <div>
          <span className="eyebrow">Локальный pack_local skill из вашего контента</span>
          <h3>Создать новый skill</h3>
        </div>
        <button className="icon-button" type="button" onClick={onCancel} aria-label="Закрыть создание skill"><X size={18} /></button>
      </header>

      <div className="create-skill-id">
        <label>
          Новый уникальный skill_id
          <input
            name="skill_id"
            autoFocus
            value={newSkillId}
            onChange={(event) => setNewSkillId(event.target.value)}
            pattern="[a-z][a-z0-9_-]{1,63}"
          />
        </label>
        <button
          className="secondary-button"
          type="button"
          onClick={requestPreview}
          disabled={!newSkillId.trim() || !effectiveContent || previewMutation.isPending}
        >
          Обновить предпросмотр
        </button>
      </div>

      <div className="create-skill-content">
        <label>
          Контент SKILL.md
          <textarea
            value={effectiveContent}
            onChange={(event) => { setContent(event.target.value); setContentTouched(true); }}
            rows={24}
            spellCheck={false}
            aria-label="Контент SKILL.md"
          />
        </label>
      </div>

      {previewMutation.isPending ? <p className="muted-copy">Валидируем frontmatter и целевое место…</p> : null}
      {previewMutation.isError ? <ErrorState error={previewMutation.error} /> : null}
      {preview ? (
        <div className="create-preview">
          <dl className="surface-detail-list">
            <div><dt>Место</dt><dd><code>{preview.destination}</code></dd></div>
            <div><dt>Владение</dt><dd>{statusLabel(preview.ownership_after_create.trust_class)} · {localizedCatalogLabel(preview.ownership_after_create.pack_id, preview.ownership_after_create.pack_id)}</dd></div>
            <div><dt>Статус проверки</dt><dd><StatusPill status={preview.validation.effective_test_status} /></dd></div>
            <div><dt>proposed_source_sha256</dt><dd><code>{preview.proposed_source_sha256.slice(0, 12)}…</code></dd></div>
          </dl>
          <p><AlertTriangle size={15} /> Skill появится в каталоге только после подтверждения. Шаблон можно править напрямую.</p>
        </div>
      ) : null}
      {createMutation.isError ? <ErrorState error={createMutation.error} /> : null}

      <footer>
        <button className="secondary-button" type="button" onClick={onCancel}>Отмена</button>
        <button
          className="primary-button"
          type="button"
          onClick={create}
          disabled={!previewMatches || createMutation.isPending}
        >
          <Plus size={15} />{createMutation.isPending ? "Создаём…" : "Подтвердить и создать"}
        </button>
      </footer>
    </section>
  );
}
