import { CircleAlert, TriangleAlert, Info, ArrowUpRight } from 'lucide-react';
import type { Issue, Process } from '../types';

export default function Finding({
  issue,
  process,
  onLocate,
}: {
  issue: Issue;
  process: Process | null;
  onLocate(): void;
}) {
  const Icon =
    issue.severity === 'error' ? CircleAlert : issue.severity === 'warning' ? TriangleAlert : Info;
  const names = issue.node_ids
    .map((id) => process?.nodes.find((n) => n.id === id)?.name)
    .filter(Boolean);
  return (
    <button
      className={'issue ' + issue.severity}
      onClick={onLocate}
      disabled={!issue.node_ids.length}
    >
      <span className="issue-label">
        <Icon size={14} />
        {
          {
            error: 'Ошибка',
            warning: 'Предупреждение',
            clarification: 'Уточнение',
            info: 'Информация',
          }[issue.severity]
        }
        <span className="finding-origin" title={issue.spec_section || undefined}>
          {issue.origin === 'llm'
            ? 'AI · бизнес'
            : {
                BPMN_SPEC: 'BPMN',
                BUSINESS_LOGIC: 'Бизнес',
                MODEL_QUALITY: 'Качество',
                LAYOUT: 'Размещение',
                XSD: 'XML / XSD',
              }[issue.source || 'MODEL_QUALITY']}
        </span>
      </span>
      <span className="finding-message">{issue.message}</span>
      {!!names.length && <span className="finding-evidence">{names.join(' · ')}</span>}
      {!!issue.node_ids.length && (
        <span className="issue-link">
          Показать на схеме <ArrowUpRight size={13} />
        </span>
      )}
    </button>
  );
}
