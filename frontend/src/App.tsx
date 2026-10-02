import { useEffect, useRef, useState } from 'react';
import Modeler from 'bpmn-js/lib/Modeler';
import {
  Activity,
  ArrowRight,
  Check,
  CheckCheck,
  ChevronDown,
  Download,
  FileUp,
  GitBranch,
  History,
  LoaderCircle,
  Maximize2,
  MessageSquare,
  Minus,
  Plus,
  Redo2,
  Send,
  ShieldCheck,
  Sparkles,
  Undo2,
  X,
  CircleAlert,
  FileText,
  MousePointer2,
} from 'lucide-react';
import Editor, { type EditorHandle } from './components/Editor';
import { api } from './services/api';
import type { Process, Result, Audit, Snapshot, LLMMetadata } from './types';

type Panel = 'copilot' | 'audit' | 'history';
function modelLabel(model: string) {
  return model.startsWith('gpt://')
    ? model.slice(6).split('/').slice(1).join('/')
    : (model.split('/').pop() ?? model).replace(/:free$/, '');
}
interface Example {
  name: string;
  text: string;
}

async function checkImport(xml: string) {
  const container = document.createElement('div');
  const modeler = new Modeler({ container });
  try {
    const { warnings } = await modeler.importXML(xml);
    if (warnings.length)
      throw new Error(
        'BPMN содержит неподдерживаемые элементы или потерянные ссылки. Импорт отменён: ' +
          warnings.map((w) => w.message).join('; '),
      );
  } catch (e) {
    throw new Error(e instanceof Error ? e.message : 'Не удалось импортировать BPMN.');
  } finally {
    modeler.destroy();
  }
}

export default function App() {
  const [examples, setExamples] = useState<Example[]>([]);
  const [health, setHealth] = useState<{ provider: string; configured: boolean } | null>(null);
  const [llmMetadata, setLLMMetadata] = useState<LLMMetadata | null>(null);
  const [text, setText] = useState('');
  const [process, setProcess] = useState<Process | null>(null);
  const [xml, setXml] = useState('');
  const [pending, setPending] = useState<Process | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [command, setCommand] = useState('');
  const [panel, setPanel] = useState<Panel>('copilot');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [audit, setAudit] = useState<Audit | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [versions, setVersions] = useState<Snapshot[]>([]);
  const [inputOpen, setInputOpen] = useState(false);
  const [examplesOpen, setExamplesOpen] = useState(false);
  const [proposal, setProposal] = useState<Result | null>(null);
  const [showTools, setShowTools] = useState(false);
  const editor = useRef<EditorHandle>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const guard = useRef(false);
  useEffect(() => {
    api<Example[]>('examples')
      .then(setExamples)
      .catch((e) => setError(e.message));
    api<{ provider: string; configured: boolean }>('health')
      .then(setHealth)
      .catch((e) => setError(e.message));
  }, []);
  async function run(label: string, job: () => Promise<void>) {
    if (guard.current) return;
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
    guard.current = true;
    setBusy(label);
    setError('');
    setNotice('');
    try {
      await job();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Не удалось выполнить действие.');
    } finally {
      guard.current = false;
      setBusy('');
    }
  }
  async function current() {
    if (!editor.current || !process) throw new Error('Сначала создайте или загрузите процесс.');
    const liveXml = await editor.current.xml();
    const synced = await api<{ process: Process }>('process/import', {
      xml: liveXml,
      previous: process,
    });
    return { xml: liveXml, process: synced.process };
  }
  async function accept(result: Result, label: string) {
    setLLMMetadata(result.metadata ?? null);
    if (!result.xml) {
      setPending(result.process);
      setAnswers({});
      setPanel('copilot');
      setProposal(null);
      setInputOpen(false);
      return;
    }
    await checkImport(result.xml);
    if (xml && editor.current) {
      const live = await editor.current.xml();
      setVersions((v) => [
        ...v.slice(-19),
        { xml: live, process, label: process?.name || 'Предыдущая версия' },
      ]);
    }
    setXml(result.xml);
    setProcess(result.process);
    setPending(null);
    setSelected(null);
    setDirty(false);
    setAudit(null);
    setProposal(null);
    setInputOpen(false);
    setNotice(label);
  }
  function generate() {
    void run('PULSE анализирует описание и проверяет процесс…', async () => {
      const result = await api<Result>('process/generate', { text });
      await accept(result, 'Процесс построен. Элементы можно редактировать на полотне.');
    });
  }
  function modify() {
    void run('PULSE вносит изменения и проверяет связи…', async () => {
      const before = await current();
      const result = await api<Result>('process/modify', { process: before.process, command });
      const added = result.process.nodes.filter(
        (n) => !before.process.nodes.some((old) => old.id === n.id),
      );
      const removed = before.process.nodes.filter(
        (n) => !result.process.nodes.some((next) => next.id === n.id),
      );
      const changed = result.process.nodes.filter((n) => {
        const old = before.process.nodes.find((o) => o.id === n.id);
        return old && JSON.stringify(old) !== JSON.stringify(n);
      });
      const diff = [
        ...added.map((n) => '+ ' + n.name),
        ...removed.map((n) => '− ' + n.name),
        ...changed.map((n) => 'Изменено: ' + n.name),
      ];
      await accept(result, diff.length ? diff.join(' · ') : 'Обновлены связи и условия процесса.');
      setCommand('');
    });
  }
  function check() {
    setPanel('audit');
    void run('Проверяем XML, граф и бизнес-логику…', async () => {
      const liveXml = await editor.current!.xml();
      await checkImport(liveXml);
      const report = await api<Audit>('process/audit', { xml: liveXml, previous: process });
      setAudit(report);
      if (report.metadata) setLLMMetadata(report.metadata);
    });
  }
  function improve() {
    void run('PULSE готовит предложение TO-BE…', async () => {
      const before = await current();
      const result = await api<Result>('process/modify', {
        process: before.process,
        command:
          'Предложи TO-BE: упрости процесс, сохрани юридическую проверку и все обязательные проверки. Не выдумывай численные KPI.',
      });
      if (!result.xml) await accept(result, '');
      else {
        await checkImport(result.xml);
        setProposal(result);
        setLLMMetadata(result.metadata ?? null);
        setPanel('copilot');
      }
    });
  }
  function download() {
    void run('Подготавливаем BPMN…', async () => {
      const liveXml = await editor.current!.xml();
      await checkImport(liveXml);
      const blob = new Blob([liveXml], { type: 'application/xml;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download =
        (process?.name || 'pulse-process').replace(/[<>:"/\\|?*\x00-\x1f]/g, '-').slice(0, 100) +
        '.bpmn';
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setNotice('BPMN сохранён с текущими ручными изменениями.');
    });
  }
  async function upload(file: File) {
    await run('Открываем BPMN…', async () => {
      if (file.size > 2_000_000) throw new Error('Для MVP загрузите BPMN размером до 2 МБ.');
      const contents = await file.text();
      await checkImport(contents);
      let imported: Process | null = null;
      try {
        imported = (await api<{ process: Process }>('process/import', { xml: contents })).process;
      } catch {
        setNotice(
          'Файл открыт. AI-операции доступны только для поддерживаемого подмножества BPMN. Редактирование и экспорт работают.',
        );
      }
      if (xml && editor.current) {
        const live = await editor.current.xml();
        setVersions((v) => [
          ...v.slice(-19),
          { xml: live, process, label: process?.name || 'До импорта' },
        ]);
      }
      setXml(contents);
      setProcess(imported);
      setDirty(false);
      setSelected(null);
      setPending(null);
      setAudit(null);
      setProposal(null);
    });
  }
  function restore(index: number) {
    void run('Восстанавливаем версию…', async () => {
      const snapshot = versions[index];
      const liveXml = await editor.current!.xml();
      setVersions((v) => [
        ...v.filter((_, i) => i !== index),
        { xml: liveXml, process, label: 'До восстановления' },
      ]);
      let synced: Process | null = null;
      try {
        synced = (
          await api<{ process: Process }>('process/import', {
            xml: snapshot.xml,
            previous: snapshot.process,
          })
        ).process;
      } catch {
        /* Unsupported files remain editable; AI is explicitly disabled. */
      }
      setXml(snapshot.xml);
      setProcess(synced);
      setSelected(null);
      setDirty(false);
      setAudit(null);
      setPending(null);
      setProposal(null);
      setNotice('Предыдущая версия восстановлена.');
    });
  }
  const node = process?.nodes.find((n) => n.id === selected);
  const ready = !!xml;
  const aiReady = ready && !!process && !pending && !busy;
  const blocking = !!busy;
  const sample = (index: number) => {
    setText(examples[index]?.text || '');
    setExamplesOpen(false);
    if (ready) setInputOpen(true);
  };
  const proposalChanges =
    proposal && process
      ? [
          ...proposal.process.nodes
            .filter((n) => !process.nodes.some((o) => o.id === n.id))
            .map((n) => '+ ' + n.name),
          ...process.nodes
            .filter((n) => !proposal.process.nodes.some((o) => o.id === n.id))
            .map((n) => '− ' + n.name),
          ...proposal.process.nodes
            .filter((n) => {
              const old = process.nodes.find((o) => o.id === n.id);
              return (
                old &&
                (old.name !== n.name ||
                  old.type !== n.type ||
                  old.participant_id !== n.participant_id)
              );
            })
            .map((n) => 'Изменено: ' + n.name),
          ...(JSON.stringify(process.flows) !== JSON.stringify(proposal.process.flows)
            ? ['Изменены порядок, связи или условия']
            : []),
        ]
      : [];
  return (
    <div className="app">
      <header className="header">
        <a className="brand" href="#" onClick={(e) => e.preventDefault()} aria-label="PULSE">
          <span className="brand-icon">
            <Activity size={21} strokeWidth={2.6} />
          </span>
          PULSE<span className="brand-caption">AI Business Process Engineer</span>
        </a>
        <nav aria-label="Действия с процессом">
          <button className="nav-button" onClick={() => setInputOpen(true)} disabled={blocking}>
            <Plus size={16} />
            Создать
          </button>
          <button className="nav-button" disabled={!ready || blocking || !!pending} onClick={check}>
            <ShieldCheck size={16} />
            Проверить
          </button>
          <button className="nav-button" disabled={!aiReady} onClick={improve}>
            <Sparkles size={16} />
            Улучшить
          </button>
          <span className="nav-divider" />
          <button
            className="primary export"
            disabled={!ready || blocking || !!pending}
            onClick={download}
          >
            <Download size={16} />
            Экспорт BPMN
          </button>
        </nav>
      </header>
      <div className="projectbar">
        <div>
          <span className="workspace-label">РАБОЧАЯ ОБЛАСТЬ</span>
          <span className="project-name">
            {process?.name || (ready ? 'Импортированный процесс' : 'Новый процесс')}
          </span>
          {dirty && <span className="muted">· Ручные изменения</span>}
        </div>
        <button
          className="text-button"
          disabled={blocking}
          onClick={() => fileInput.current?.click()}
        >
          <FileUp size={15} />
          Открыть .bpmn
        </button>
      </div>
      <input
        ref={fileInput}
        type="file"
        accept=".bpmn,.xml"
        hidden
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) void upload(file);
          e.target.value = '';
        }}
      />
      <main className="workspace">
        <section
          className={'canvas-area ' + (ready ? 'has-diagram' : '')}
          aria-busy={blocking}
          aria-label="Рабочая область процесса"
        >
          {ready ? (
            <>
              <Editor
                ref={editor}
                xml={xml}
                showTools={showTools}
                onSelect={setSelected}
                onError={setError}
                onChange={() => {
                  setDirty(true);
                  setAudit(null);
                  setProposal(null);
                  setSelected(null);
                }}
              />
              <div className="canvas-heading">
                <span className="status-dot" />
                BPMN 2.0<span className="muted">Редактируемая модель</span>
              </div>
              <div className="canvas-tools">
                <button
                  title="Инструменты BPMN"
                  aria-label="Инструменты BPMN"
                  aria-pressed={showTools}
                  onClick={() => setShowTools((v) => !v)}
                >
                  <MousePointer2 size={17} />
                </button>
                <span />
                <button
                  title="Отменить ручное изменение"
                  aria-label="Отменить ручное изменение"
                  onClick={() => editor.current?.undo()}
                  disabled={blocking}
                >
                  <Undo2 size={17} />
                </button>
                <button
                  title="Повторить ручное изменение"
                  aria-label="Повторить ручное изменение"
                  onClick={() => editor.current?.redo()}
                  disabled={blocking}
                >
                  <Redo2 size={17} />
                </button>
                <span />
                <button
                  title="Уменьшить"
                  aria-label="Уменьшить"
                  onClick={() => editor.current?.zoom(-0.15)}
                >
                  <Minus size={17} />
                </button>
                <button
                  title="Увеличить"
                  aria-label="Увеличить"
                  onClick={() => editor.current?.zoom(0.15)}
                >
                  <Plus size={17} />
                </button>
                <button className="fit" onClick={() => editor.current?.fit()}>
                  <Maximize2 size={16} />
                  По размеру
                </button>
              </div>
              <div className="canvas-footer">
                <GitBranch size={14} />
                {process
                  ? `${process.participants.length} участников · ${process.nodes.filter((n) => n.type.endsWith('task')).length} действий · ${process.flows.length} связей`
                  : 'Файл BPMN'}
                <span>Перетаскивайте элементы для редактирования</span>
              </div>
            </>
          ) : (
            <div className="start-screen">
              <div className="eyebrow">
                <span />
                ОТ ОПИСАНИЯ К ПРОЦЕССУ
              </div>
              <h1>
                Сложный процесс.
                <br />
                <span>Понятная схема.</span>
              </h1>
              <p className="intro">
                Опишите, как устроена работа. PULSE выделит участников,
                <br className="desktop-break" /> уточнит важные детали и построит редактируемую
                BPMN-модель.
              </p>
              <div className="input-sheet">
                <label htmlFor="description">Опишите бизнес-процесс обычным языком</label>
                <textarea
                  id="description"
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  maxLength={20000}
                  disabled={blocking}
                  placeholder="Клиент отправляет заявку. Оператор проверяет документы. Если документов не хватает, заявку возвращают на доработку. Затем юрист и служба безопасности проводят проверки одновременно…"
                />
                <div className="input-bottom">
                  <div className="example-picker">
                    <button
                      className="text-button"
                      disabled={blocking || !examples.length}
                      onClick={() => setExamplesOpen((v) => !v)}
                    >
                      <FileText size={16} />
                      Загрузить пример
                      <ChevronDown size={14} />
                    </button>
                    {examplesOpen && (
                      <div className="example-menu">
                        {examples.map((e, i) => (
                          <button key={e.name} onClick={() => sample(i)}>
                            {e.name}
                            <ArrowRight size={14} />
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                  <button
                    className="primary generate"
                    onClick={generate}
                    disabled={text.trim().length < 10 || blocking}
                  >
                    {blocking ? (
                      <LoaderCircle className="spin" size={17} />
                    ) : (
                      <Sparkles size={17} />
                    )}
                    Создать BPMN
                    <ArrowRight size={16} />
                  </button>
                </div>
              </div>
              <div className="steps">
                <span>
                  <span className="step-no">01</span>Описание
                </span>
                <ArrowRight size={14} />
                <span>
                  <span className="step-no">02</span>Уточнение
                </span>
                <ArrowRight size={14} />
                <span>
                  <span className="step-no">03</span>BPMN-модель
                </span>
              </div>
              <div className="trust-note">
                <CheckCheck size={15} />
                Проверка структуры и XML · Редактирование в bpmn.io · Экспорт .bpmn
              </div>
            </div>
          )}
          {busy && (
            <div className="working" role="status">
              <LoaderCircle className="spin" size={18} />
              {busy}
            </div>
          )}
        </section>
        <aside className="copilot">
          <div className="copilot-heading">
            <span className="copilot-mark">
              <Activity size={19} />
            </span>
            <div>
              <strong>PULSE Copilot</strong>
              <span>Ваш помощник по процессам</span>
            </div>
            <span className={'provider-dot ' + (health?.configured ? 'online' : '')} />
          </div>
          <div className="panel-tabs" role="tablist" aria-label="Панель помощника">
            {(
              [
                ['copilot', MessageSquare, 'Помощник'],
                ['audit', ShieldCheck, 'Проверка'],
                ['history', History, 'История'],
              ] as const
            ).map(([id, Icon, label]) => (
              <button
                key={id}
                role="tab"
                aria-selected={panel === id}
                className={panel === id ? 'active' : ''}
                onClick={() => setPanel(id)}
              >
                <Icon size={15} />
                {label}
              </button>
            ))}
          </div>
          <div className="panel-content">
            <div className="provider-info">
              {llmMetadata && llmMetadata.provider_used !== 'mock' ? (
                <div role="status" aria-live="polite">
                  <span className="provider-name">
                    {llmMetadata.fallback_used
                      ? 'Резервная модель активирована' +
                        (llmMetadata.provider_used.includes('gemini') ? ': Gemini' : '')
                      : 'Модель: ' + modelLabel(llmMetadata.model_used)}
                  </span>
                  <p>
                    {llmMetadata.fallback_used
                      ? 'Модель: ' + modelLabel(llmMetadata.model_used)
                      : 'Использована для последнего AI-ответа.'}
                  </p>
                </div>
              ) : !health ? (
                'Подключение к backend…'
              ) : health.provider === 'mock' ? (
                <>
                  <span className="demo-pill">ТЕСТОВЫЙ РЕЖИМ</span>
                  <p>Три встроенных примера и одна команда изменения. Модель не вызывается.</p>
                </>
              ) : (
                <>
                  <span className="provider-name">{health.provider}</span>
                  <p>
                    {health.configured
                      ? 'Модель подключается через backend API.'
                      : 'Укажите API-ключ и модель в .env backend.'}
                  </p>
                </>
              )}
            </div>
            {error && (
              <div className="feedback error" role="alert">
                <CircleAlert size={17} />
                <div>{error}</div>
                <button aria-label="Скрыть ошибку" onClick={() => setError('')}>
                  <X size={14} />
                </button>
              </div>
            )}
            {notice && (
              <div className="feedback success" role="status">
                <Check size={17} />
                <div>{notice}</div>
              </div>
            )}
            {panel === 'copilot' && (
              <>
                {pending ? (
                  <div className="clarify">
                    <div className="section-kicker">УТОЧНЕНИЕ ПРОЦЕССА</div>
                    <h2>Нужно уточнить {pending.ambiguities.length} момента</h2>
                    <p className="muted">Подтвердите важные детали перед построением схемы.</p>
                    {pending.ambiguities.map((a, i) => (
                      <fieldset key={a.id}>
                        <legend>
                          {i + 1}. {a.question}
                        </legend>
                        <div className="answer-options">
                          {a.suggested_answers.map((answer) => (
                            <button
                              key={answer}
                              disabled={blocking}
                              className={answers[a.id] === answer ? 'chosen' : ''}
                              onClick={() => setAnswers((v) => ({ ...v, [a.id]: answer }))}
                            >
                              {answers[a.id] === answer && <Check size={13} />} {answer}
                            </button>
                          ))}
                        </div>
                        <input
                          aria-label={'Свой ответ: ' + a.question}
                          placeholder="Или свой ответ…"
                          value={
                            a.suggested_answers.includes(answers[a.id]) ? '' : answers[a.id] || ''
                          }
                          onChange={(e) => setAnswers((v) => ({ ...v, [a.id]: e.target.value }))}
                          disabled={blocking}
                        />
                      </fieldset>
                    ))}
                    <button
                      className="primary full"
                      disabled={blocking || pending.ambiguities.some((a) => !answers[a.id]?.trim())}
                      onClick={() =>
                        void run('PULSE учитывает ответы и проверяет процесс…', async () => {
                          const result = await api<Result>('process/clarify', {
                            process: pending,
                            answers,
                          });
                          await accept(result, 'Ответы учтены. BPMN построен.');
                        })
                      }
                    >
                      Подтвердить и построить
                      <ArrowRight size={15} />
                    </button>
                    <button
                      className="text-button cancel"
                      disabled={blocking}
                      onClick={() => {
                        setPending(null);
                        setAnswers({});
                      }}
                    >
                      Отменить уточнение
                    </button>
                  </div>
                ) : (
                  <>
                    {proposal && (
                      <div className="proposal">
                        <div className="section-kicker">AS-IS → TO-BE</div>
                        <h2>Предложение улучшения</h2>
                        <p>Текущая версия сохранится в истории.</p>
                        <div className="comparison">
                          <div>
                            <strong>AS-IS</strong>
                            <span>{process?.nodes.length} элементов</span>
                            <span>{process?.flows.length} связей</span>
                          </div>
                          <div>
                            <strong>TO-BE</strong>
                            <span>{proposal.process.nodes.length} элементов</span>
                            <span>{proposal.process.flows.length} связей</span>
                          </div>
                        </div>
                        <ul>
                          {proposalChanges.length ? (
                            proposalChanges.map((c) => <li key={c}>{c}</li>)
                          ) : (
                            <li>Изменения в составе узлов и связей не обнаружены.</li>
                          )}
                        </ul>
                        <div className="proposal-actions">
                          <button
                            className="primary"
                            disabled={blocking}
                            onClick={() =>
                              void run('Применяем TO-BE…', () =>
                                accept(proposal, 'TO-BE применён. AS-IS доступен в истории.'),
                              )
                            }
                          >
                            Применить
                          </button>
                          <button disabled={blocking} onClick={() => setProposal(null)}>
                            Отклонить
                          </button>
                        </div>
                      </div>
                    )}
                    {node && !dirty ? (
                      <div className="evidence">
                        <div className="section-kicker">ИСТОЧНИК ЭЛЕМЕНТА</div>
                        <h2>{node.name}</h2>
                        <dl>
                          <dt>Участник</dt>
                          <dd>
                            {process?.participants.find((p) => p.id === node.participant_id)?.name}
                          </dd>
                          <dt>Основание в тексте</dt>
                          <dd>
                            {node.source_text ? (
                              <blockquote>«{node.source_text}»</blockquote>
                            ) : (
                              'Прямой источник не указан'
                            )}
                          </dd>
                          <dt>Подтверждение</dt>
                          <dd>
                            {
                              {
                                high: 'Есть прямой источник',
                                medium: 'Нужна проверка аналитика',
                                confirmation_required: 'Требует подтверждения',
                              }[node.confidence]
                            }
                          </dd>
                        </dl>
                        {node.inferred && (
                          <p className="inferred">
                            Элемент содержит интерпретацию. Проверьте его смысл.
                          </p>
                        )}
                      </div>
                    ) : ready ? (
                      <div className="guide">
                        <div className="section-kicker">РАБОТА С МОДЕЛЬЮ</div>
                        <h2>Схема готова к работе</h2>
                        <p>
                          {dirty
                            ? 'Схема изменена вручную. Перед AI-операцией текущие элементы и связи будут синхронизированы с моделью.'
                            : 'Выберите элемент на полотне, чтобы увидеть участника и источник в описании.'}
                        </p>
                        <div className="guide-line">
                          <ShieldCheck size={18} />
                          <span>Проверьте структуру и бизнес-логику</span>
                        </div>
                        <div className="guide-line">
                          <MessageSquare size={18} />
                          <span>Измените процесс обычной фразой</span>
                        </div>
                        <div className="guide-line">
                          <Download size={18} />
                          <span>Передайте BPMN бизнес-аналитику</span>
                        </div>
                      </div>
                    ) : (
                      <div className="welcome">
                        <div className="section-kicker">НАЧНЁМ С ВАШЕГО ПРОЦЕССА</div>
                        <h2>
                          Опишите работу.
                          <br />
                          Остальное — вместе.
                        </h2>
                        <p>
                          Можно начать с короткого описания. Укажите, кто выполняет действия, что
                          происходит при отказе и какие проверки идут параллельно.
                        </p>
                        <div className="welcome-rule" />
                        <div className="guide-line">
                          <GitBranch size={19} />
                          <div>
                            <strong>Структура, а не рисунок</strong>
                            <p>Участники, условия и связи в настоящей BPMN 2.0 модели.</p>
                          </div>
                        </div>
                        <div className="guide-line">
                          <ShieldCheck size={19} />
                          <div>
                            <strong>Проверяемый результат</strong>
                            <p>Программная валидация до построения диаграммы.</p>
                          </div>
                        </div>
                        <div className="guide-line">
                          <MessageSquare size={19} />
                          <div>
                            <strong>Важные вопросы вовремя</strong>
                            <p>Неоднозначные решения требуют подтверждения.</p>
                          </div>
                        </div>
                      </div>
                    )}
                  </>
                )}
              </>
            )}
            {panel === 'audit' && (
              <div className="audit">
                <div className="section-kicker">BPMN DOCTOR</div>
                <h2>Проверка процесса</h2>
                {audit ? (
                  <>
                    <h3>Техническая проверка</h3>
                    <div className="checks">
                      <div>
                        <Check size={16} />
                        Импорт в bpmn-js
                      </div>
                      {Object.entries(audit.technical).map(([label, ok]) => (
                        <div key={label} className={ok ? '' : 'failed'}>
                          {ok ? <Check size={16} /> : <CircleAlert size={16} />} {label}
                        </div>
                      ))}
                    </div>
                    <h3>Бизнес-проверка</h3>
                    <p className="muted">
                      {audit.llm_audit
                        ? 'Программные правила + рекомендации модели'
                        : 'Программные правила. Аудит модели не выполнялся.'}
                    </p>
                    {audit.notice && <p className="muted">{audit.notice}</p>}
                    <div className="issues">
                      {audit.issues.map((issue, i) => (
                        <button
                          key={i}
                          className={'issue ' + issue.severity}
                          onClick={() => editor.current?.highlight(issue.node_ids)}
                          disabled={!issue.node_ids.length}
                        >
                          <span className="issue-label">
                            {
                              { error: 'Ошибка', warning: 'Проверьте', info: 'Рекомендация' }[
                                issue.severity
                              ]
                            }
                            {issue.origin === 'llm' ? ' · Модель' : ''}
                          </span>
                          {issue.message}
                          {issue.node_ids.length > 0 && (
                            <span className="issue-link">Показать на схеме →</span>
                          )}
                        </button>
                      ))}
                    </div>
                  </>
                ) : (
                  <p className="muted">
                    {ready
                      ? 'Запустите проверку, чтобы найти ошибки структуры и вопросы к бизнес-логике.'
                      : 'Сначала создайте или загрузите BPMN-процесс.'}
                  </p>
                )}
                <button
                  className="secondary full"
                  disabled={!ready || blocking || !!pending}
                  onClick={check}
                >
                  <ShieldCheck size={16} />
                  {audit ? 'Проверить снова' : 'Проверить процесс'}
                </button>
              </div>
            )}
            {panel === 'history' && (
              <div className="history">
                <div className="section-kicker">ВЕРСИИ ПРОЦЕССА</div>
                <h2>История изменений</h2>
                <p className="muted">
                  До 20 предыдущих версий в текущей сессии. Ручные правки отменяются кнопками на
                  полотне.
                </p>
                {versions.length ? (
                  [...versions].reverse().map((v, i) => (
                    <button
                      className="version"
                      disabled={blocking}
                      key={versions.length - i}
                      onClick={() => restore(versions.length - 1 - i)}
                    >
                      <History size={17} />
                      <div>
                        <strong>{v.label}</strong>
                        <span>Версия {versions.length - i} · Восстановить</span>
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
            )}
          </div>
          <div className="command-area">
            <label htmlFor="command">Изменить процесс</label>
            <div className="command-box">
              <textarea
                id="command"
                value={command}
                onChange={(e) => setCommand(e.target.value)}
                disabled={!aiReady}
                maxLength={4000}
                placeholder={ready ? 'Что изменить в процессе?' : 'Сначала создайте BPMN-модель'}
                onKeyDown={(e) => {
                  if (
                    e.key === 'Enter' &&
                    (e.ctrlKey || e.metaKey) &&
                    aiReady &&
                    command.trim().length > 2
                  )
                    modify();
                }}
              />
              <button
                className="send"
                aria-label="Отправить команду"
                onClick={modify}
                disabled={!aiReady || command.trim().length < 3}
              >
                <Send size={17} />
              </button>
            </div>
            {aiReady && (
              <button
                className="command-suggestion"
                onClick={() =>
                  setCommand('После проверки документов добавь согласование руководителем')
                }
              >
                + Согласование после проверки документов
              </button>
            )}
            <span className="command-hint">
              {ready
                ? 'Ctrl + Enter — отправить · AI-изменения сохраняются в истории'
                : 'Ключи модели хранятся только на сервере'}
            </span>
          </div>
        </aside>
      </main>
      <footer className="app-footer">
        <span>
          <span className="status-dot" />
          PULSE / Рабочий прототип
        </span>
        <span>Текст → Структура → Проверка → BPMN 2.0</span>
        <span>Энергетика · Полуфинал</span>
      </footer>
      {inputOpen && (
        <div
          className="modal-backdrop"
          onClick={(e) => {
            if (e.target === e.currentTarget && !blocking) setInputOpen(false);
          }}
        >
          <section
            className="input-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="new-title"
          >
            <button
              className="modal-close"
              aria-label="Закрыть"
              disabled={blocking}
              onClick={() => setInputOpen(false)}
            >
              <X size={20} />
            </button>
            <div className="section-kicker">НОВЫЙ ПРОЦЕСС</div>
            <h2 id="new-title">От описания к BPMN</h2>
            <p className="muted">Текущая схема сохранится в истории при успешной генерации.</p>
            <label htmlFor="new-description">Описание процесса</label>
            <textarea
              id="new-description"
              autoFocus
              value={text}
              disabled={blocking}
              onChange={(e) => setText(e.target.value)}
              maxLength={20000}
            />
            <div className="modal-examples">
              {examples.map((e, i) => (
                <button disabled={blocking} key={e.name} onClick={() => sample(i)}>
                  {e.name}
                </button>
              ))}
            </div>
            {error && <p className="modal-error">{error}</p>}
            <button
              className="primary full"
              disabled={blocking || text.trim().length < 10}
              onClick={generate}
            >
              {blocking ? <LoaderCircle className="spin" size={17} /> : <Sparkles size={17} />}
              Создать BPMN
            </button>
          </section>
        </div>
      )}
    </div>
  );
}
