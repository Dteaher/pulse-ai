import { History, Undo2 } from 'lucide-react';
import type { Snapshot } from '../types';

interface Props {
  ready: boolean;
  version: number;
  description: string;
  processName?: string;
  versions: Snapshot[];
  disabled: boolean;
  onRestore: (index: number) => void;
}
export default function HistoryPanel({
  ready,
  version,
  description,
  processName,
  versions,
  disabled,
  onRestore,
}: Props) {
  return (
    <div className="history">
      <div className="section-kicker">ВЕРСИИ ПРОЦЕССА</div>
      <h2>История изменений</h2>
      {ready && (
        <div className="current-version">
          <strong>v{version || 1} · Текущая версия</strong>
          <p>{description || processName}</p>
        </div>
      )}
      <p className="muted">
        До 20 предыдущих версий в текущей сессии. Ручные правки отменяются кнопками на полотне.
      </p>
      {versions.length ? (
        [...versions].reverse().map((v, i) => (
          <button
            className="version"
            disabled={disabled}
            key={versions.length - i}
            onClick={() => onRestore(versions.length - 1 - i)}
          >
            <span className="version-number">v{v.version ?? versions.length - i}</span>
            <div>
              <strong title={v.label}>{v.process?.name || v.label}</strong>
              <span>{v.label}</span>
              <span>
                {new Date(v.timestamp).toLocaleTimeString('ru-RU', {
                  hour: '2-digit',
                  minute: '2-digit',
                })}{' '}
                · Восстановить версию
              </span>
            </div>
            <Undo2 size={15} />
          </button>
        ))
      ) : (
        <div className="empty-panel">
          <History size={25} />
          <p>Предыдущие версии появятся после изменения или замены схемы.</p>
        </div>
      )}
    </div>
  );
}
