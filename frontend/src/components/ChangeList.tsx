import type { Change } from '../types';
const labels = {
  branching: 'Ветвление',
  condition: 'Условия',
  participant: 'Участники',
  structure: 'Структура',
};
const actions = [
  { id: 'added', label: 'Добавлено', symbol: '+' },
  { id: 'changed', label: 'Изменено', symbol: '~' },
  { id: 'removed', label: 'Удалено', symbol: '−' },
];
export default function ChangeList({ changes }: { changes: Change[] }) {
  const action = (c: Change) =>
    c.action ??
    (c.type.endsWith('_added') ? 'added' : c.type.endsWith('_removed') ? 'removed' : 'changed');
  return (
    <div className="change-groups">
      {actions.map((group) => {
        const items = changes.filter((c) => action(c) === group.id);
        if (!items.length) return null;
        const row = (c: Change, i: number) => (
          <li key={i}>
            <span className={'diff-sign ' + group.id}>{group.symbol}</span>
            <div>
              {c.description}
              <small>{labels[c.category ?? 'structure']}</small>
            </div>
          </li>
        );
        return (
          <section key={group.id} className="change-group" aria-label={group.label}>
            <h3>
              {group.label}
              <span>{items.length}</span>
            </h3>
            <ul>{items.slice(0, 4).map(row)}</ul>
            {items.length > 4 && (
              <details>
                <summary>Ещё {items.length - 4}</summary>
                <ul>{items.slice(4).map(row)}</ul>
              </details>
            )}
          </section>
        );
      })}
    </div>
  );
}
