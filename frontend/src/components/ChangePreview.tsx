import type { Result } from '../types';
import ChangeList from './ChangeList';

export default function ChangePreview({
  result,
  nextVersion,
  instruction,
  busy,
  onApply,
  onCancel,
}: {
  result: Result;
  nextVersion: number;
  instruction: string;
  busy: boolean;
  onApply(): void;
  onCancel(): void;
}) {
  const changes = result.changes ?? [];
  const action = (c: (typeof changes)[number]) =>
    c.action ??
    (c.type.endsWith('_added') ? 'added' : c.type.endsWith('_removed') ? 'removed' : 'changed');
  return (
    <section className="change-preview" aria-label="Предпросмотр изменений">
      <div className="section-kicker">ПЕРЕД ПРИМЕНЕНИЕМ · v{nextVersion}</div>
      <h2>Предпросмотр изменений</h2>
      <p className="preview-instruction">
        {instruction.length > 160 ? instruction.slice(0, 160) + '…' : instruction}
      </p>
      {instruction.length > 160 && (
        <details className="preview-command-details">
          <summary>Показать команду полностью</summary>
          <p>{instruction}</p>
        </details>
      )}
      <p className="muted">
        Текущая диаграмма сохранена. Новая версия появится после подтверждения.
      </p>
      <div className="preview-counts" aria-label="Состав изменений">
        <span>
          Добавится <strong>{changes.filter((c) => action(c) === 'added').length}</strong>
        </span>
        <span>
          Удалится <strong>{changes.filter((c) => action(c) === 'removed').length}</strong>
        </span>
        <span>
          Изменится <strong>{changes.filter((c) => action(c) === 'changed').length}</strong>
        </span>
      </div>
      <div className="preview-actions">
        <button
          className="primary"
          aria-label="Применить изменения"
          disabled={busy}
          onClick={onApply}
        >
          Применить
        </button>
        <button className="secondary" disabled={busy} onClick={onCancel}>
          Отменить
        </button>
      </div>
      {changes.length ? (
        <ChangeList changes={changes} />
      ) : (
        <p className="muted">Изменений в составе процесса не обнаружено.</p>
      )}
      {!!result.semantic_warnings?.length && (
        <div className="semantic-notices">
          <strong>Стоит проверить</strong>
          <ul>
            {result.semantic_warnings.map((i, n) => (
              <li key={n}>{i.message}</li>
            ))}
          </ul>
        </div>
      )}
      <p className="preview-checks">
        Проверены XML, связи, условия и параллельные ветви. Полная проверка бизнес-смысла требует
        аналитика.
      </p>
    </section>
  );
}
