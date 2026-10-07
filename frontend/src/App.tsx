import { useEffect, useRef, useState, lazy, Suspense } from 'react';
import {
  PanelRightClose,
  PanelRightOpen,
  Workflow,
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
  Undo2,
  X,
  CircleAlert,
  FileText,
  MousePointer2,
} from 'lucide-react';
import HistoryPanel from './components/HistoryPanel';
import AccessGate from './components/AccessGate';
import BrandMark from './components/BrandMark';
import Finding from './components/Finding';
import type { EditorHandle } from './components/Editor';
import { api } from './services/api';
import { checkImport } from './services/bpmnImport';
import { modelLabel } from './services/modelLabel';
import ChangeList from './components/ChangeList';
import ChangePreview from './components/ChangePreview';
import { useProcessState } from './services/useProcessState';
import type { Process, Result, Audit, Snapshot, LLMMetadata, Ambiguity } from './types';

type Panel = 'copilot' | 'audit' | 'history';
const Editor = lazy(() => import('./components/Editor'));
interface Example {
  name: string;
  text: string;
}

export default function App() {
  const [examples, setExamples] = useState<Example[]>([]);
  const [health, setHealth] = useState<{
    provider: string;
    configured: boolean;
    access_required?: boolean;
  } | null>(null);
  const [llmMetadata, setLLMMetadata] = useState<LLMMetadata | null>(null);
  const [text, setText] = useState('');
  const {
    process,
    xml,
    versions,
    changes,
    assumptions,
    version,
    description: versionDescription,
    load,
    apply,
    undo,
    reset,
  } = useProcessState();
  const [clarifyContext, setClarifyContext] = useState<{
    operation: 'generate' | 'modify';
    proposal?: boolean;
    originalText: string;
    instruction: string;
    before?: Snapshot;
    round: number;
    limitReached: boolean;
    acceptedAssumptions: Ambiguity[];
  } | null>(null);
  const [pending, setPending] = useState<Pick<Process, 'ambiguities'> | null>(null);
  const [preflight, setPreflight] = useState<Result['preflight']>();
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [command, setCommand] = useState('');
  const [acceptedQuestions, setAcceptedQuestions] = useState<string[]>([]);
  const [editPreview, setEditPreview] = useState<{
    result: Result;
    before: Snapshot;
    instruction: string;
  } | null>(null);
  const [panel, setPanel] = useState<Panel>('copilot');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [audit, setAudit] = useState<Audit | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [inputOpen, setInputOpen] = useState(false);
  const [examplesOpen, setExamplesOpen] = useState(false);
  const [proposal, setProposal] = useState<Result | null>(null);
  const [showTools, setShowTools] = useState(false);
  const [editorReady, setEditorReady] = useState(false);
  useEffect(() => {
    if (!xml) setEditorReady(false);
  }, [xml]);
  const [panelCollapsed, setPanelCollapsed] = useState(false);
  useEffect(() => {
    if (pending || editPreview || error) setPanelCollapsed(false);
  }, [pending, editPreview, error]);
  const editor = useRef<EditorHandle>(null);
  const panelContent = useRef<HTMLDivElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const guard = useRef(false);
  const modal = useRef<HTMLElement>(null);
  const examplePicker = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!inputOpen) return;
    const previousFocus = modal.current?.contains(document.activeElement)
      ? document.querySelector<HTMLElement>('.header nav button')
      : (document.activeElement as HTMLElement | null);
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !guard.current) setInputOpen(false);
      if (event.key !== 'Tab') return;
      const controls = modal.current?.querySelectorAll<HTMLElement>(
        'button:not(:disabled), textarea:not(:disabled), input:not(:disabled), [tabindex="0"]',
      );
      if (!controls?.length) return;
      const first = controls[0],
        last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', handleKey);
    return () => {
      document.removeEventListener('keydown', handleKey);
      previousFocus?.focus();
    };
  }, [inputOpen]);
  useEffect(() => {
    if (!examplesOpen) return;
    const close = (event: PointerEvent) => {
      if (!examplePicker.current?.contains(event.target as Node)) setExamplesOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setExamplesOpen(false);
    };
    document.addEventListener('pointerdown', close);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('pointerdown', close);
      document.removeEventListener('keydown', escape);
    };
  }, [examplesOpen]);
  function home() {
    if (guard.current) return;
    reset();
    setText('');
    setCommand('');
    setPending(null);
    setPreflight(undefined);
    setAnswers({});
    setAcceptedQuestions([]);
    setClarifyContext(null);
    setEditPreview(null);
    setPanel('copilot');
    setAudit(null);
    setSelected(null);
    setDirty(false);
    setInputOpen(false);
    setExamplesOpen(false);
    setProposal(null);
    setShowTools(false);
    setPanelCollapsed(false);
    setError('');
    setNotice('');
    setLLMMetadata(null);
    requestAnimationFrame(() => document.getElementById('description')?.focus());
  }
  useEffect(() => {
    if (panelContent.current) panelContent.current.scrollTop = 0;
  }, [pending, changes, panel, editPreview, error, notice]);
  useEffect(() => {
    api<Example[]>('examples')
      .then(setExamples)
      .catch((e) => setError(e.message));
    api<{ provider: string; configured: boolean; access_required?: boolean }>('health')
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
  async function accept(result: Result, label: string, before?: Snapshot) {
    setLLMMetadata(result.metadata ?? null);
    if (result.ambiguities.length || !result.xml) {
      setPending(result.process ?? { ambiguities: result.ambiguities });
      setPreflight(result.preflight);
      setAnswers({});
      setAcceptedQuestions([]);
      setClarifyContext((c) =>
        c ? { ...c, acceptedAssumptions: result.accepted_assumptions ?? c.acceptedAssumptions } : c,
      );
      setPanel('copilot');
      setProposal(null);
      setInputOpen(false);
      return;
    }
    await checkImport(result.xml);
    let snapshot = before;
    if (!snapshot && xml && editor.current) {
      snapshot = {
        xml: await editor.current.xml(),
        process,
        label: versionDescription || process?.name || 'Предыдущая версия',
        version,
        assumptions,
        timestamp: new Date().toISOString(),
        reason: 'before_replace',
      };
    }
    apply(result, snapshot, label);
    setEditPreview(null);
    setClarifyContext(null);
    setPending(null);
    setPreflight(undefined);
    setSelected(null);
    setDirty(false);
    setAudit(null);
    setProposal(null);
    setInputOpen(false);
    setNotice(result.changes?.length ? '' : label);
  }
  function generate() {
    void run('PULSE анализирует описание и проверяет процесс…', async () => {
      setClarifyContext({
        operation: 'generate',
        originalText: text,
        instruction: '',
        round: 0,
        limitReached: false,
        acceptedAssumptions: [],
      });
      const result = await api<Result>('process/generate', { text });
      await accept(result, 'Процесс построен. Элементы можно редактировать на полотне.');
    });
  }
  function modify() {
    void run('PULSE вносит изменения и проверяет связи…', async () => {
      const before = await current();
      const snapshot: Snapshot = {
        ...before,
        label: versionDescription || 'Создан по описанию',
        version,
        assumptions,
        timestamp: new Date().toISOString(),
        reason: 'before_ai_modify',
      };
      setClarifyContext({
        operation: 'modify',
        originalText: before.process.description,
        instruction: command,
        before: snapshot,
        round: 0,
        limitReached: false,
        acceptedAssumptions: assumptions,
      });
      try {
        const result = await api<Result>('process/modify', {
          process: before.process,
          instruction: command,
          accepted_assumptions: assumptions,
        });
        await prepareChange(result, snapshot, command);
      } catch (e) {
        setClarifyContext(null);
        const detail = e instanceof Error ? e.message : 'Повторите попытку.';
        throw new Error(
          detail.startsWith('Изменение не удалось применить.')
            ? detail
            : 'Изменение не удалось применить. Текущая версия процесса сохранена. ' + detail,
        );
      }
      setCommand('');
    });
  }
  async function prepareChange(result: Result, before: Snapshot, instruction: string) {
    setLLMMetadata(result.metadata ?? null);
    if (result.ambiguities.length || !result.xml) {
      await accept(result, '');
      return;
    }
    await checkImport(result.xml);
    setEditPreview({ result, before, instruction });
    setPending(null);
    setPreflight(undefined);
    setClarifyContext(null);
    setPanel('copilot');
    setProposal(null);
    setNotice('');
  }
  function applyPreview() {
    if (!editPreview) return;
    const candidate = editPreview;
    void run('Применяем проверенную версию…', async () => {
      const liveXml = await editor.current!.xml();
      if (liveXml !== candidate.before.xml) {
        setEditPreview(null);
        setCommand(candidate.instruction);
        throw new Error(
          'Диаграмма изменилась после подготовки превью. Текущая версия сохранена. Отправьте команду повторно.',
        );
      }
      await accept(candidate.result, candidate.instruction, candidate.before);
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
      const instruction =
        'Предложи TO-BE: упрости процесс, сохрани юридическую проверку и все обязательные проверки. Не выдумывай численные KPI.';
      setClarifyContext({
        operation: 'modify',
        proposal: true,
        originalText: before.process.description,
        instruction,
        round: 0,
        limitReached: false,
        acceptedAssumptions: assumptions,
        before: {
          ...before,
          label: versionDescription || 'AS-IS',
          timestamp: new Date().toISOString(),
          reason: 'before_to_be',
          version,
          assumptions,
        },
      });
      const result = await api<Result>('process/modify', {
        process: before.process,
        command: instruction,
      });
      if (!result.xml) await accept(result, '');
      else {
        await checkImport(result.xml);
        setProposal(result);
        setClarifyContext(null);
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
      const before: Snapshot | undefined =
        xml && editor.current
          ? {
              xml: await editor.current.xml(),
              process,
              label: versionDescription || 'До импорта',
              timestamp: new Date().toISOString(),
              reason: 'before_import',
              version,
              assumptions,
            }
          : undefined;
      setEditPreview(null);
      load(imported, contents, before);
      setDirty(false);
      setSelected(null);
      setPending(null);
      setPreflight(undefined);
      setAudit(null);
      setProposal(null);
    });
  }
  function restore(index: number) {
    void run('Восстанавливаем версию…', async () => {
      const snapshot = versions[index];
      await checkImport(snapshot.xml);
      undo(index);
      setEditPreview(null);
      setClarifyContext(null);
      setSelected(null);
      setDirty(false);
      setAudit(null);
      setPending(null);
      setPreflight(undefined);
      setProposal(null);
      setNotice('Предыдущая версия восстановлена.');
    });
  }
  const node = process?.nodes.find((n) => n.id === selected);
  const ready = !!xml;
  const aiReady = ready && !!process && !pending && !busy && !editPreview;
  const blocking = !!busy || (ready && !editorReady);
  const pendingCritical = !!pending?.ambiguities.some((a) => a.severity === 'critical');
  const displayedAssumptions = [
    ...new Map([
      ...assumptions.map(
        (a) =>
          [
            a.id,
            { id: a.id, text: a.assumption || 'Оставлено как в черновике: ' + a.question },
          ] as const,
      ),
      ...(process?.assumptions ?? []).map((a) => [a.id, { id: a.id, text: a.text }] as const),
    ]).values(),
  ];
  const sample = (index: number) => {
    setText(examples[index]?.text || '');
    setExamplesOpen(false);
    if (ready) setInputOpen(true);
  };
  const proposalChanges =
    proposal && process
      ? [
          ...proposal
            .process!.nodes.filter((n) => !process.nodes.some((o) => o.id === n.id))
            .map((n) => '+ ' + n.name),
          ...process.nodes
            .filter((n) => !proposal.process!.nodes.some((o) => o.id === n.id))
            .map((n) => '− ' + n.name),
          ...proposal
            .process!.nodes.filter((n) => {
              const old = process.nodes.find((o) => o.id === n.id);
              return (
                old &&
                (old.name !== n.name ||
                  old.type !== n.type ||
                  old.participant_id !== n.participant_id)
              );
            })
            .map((n) => 'Изменено: ' + n.name),
          ...(JSON.stringify(process.flows) !== JSON.stringify(proposal.process!.flows)
            ? ['Изменены порядок, связи или условия']
            : []),
        ]
      : [];
  return (
    <div
      className={
        'app ' +
        (!ready ? 'is-home' : 'is-workspace') +
        (pending ? ' has-questions' : '') +
        (panelCollapsed && ready ? ' panel-collapsed' : '')
      }
    >
      <AccessGate required={Boolean(health?.access_required)} />
      <header className="header">
        <a
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            home();
          }}
          aria-label="PULSE"
          aria-disabled={blocking}
          title="На начальный экран"
        >
          <BrandMark />
          <span className="wordmark">PULSE</span>
        </a>
        <nav aria-label="Действия с процессом">
          <button
            className="nav-button"
            aria-label="Создать"
            onClick={() =>
              ready ? setInputOpen(true) : document.getElementById('description')?.focus()
            }
            disabled={blocking}
          >
            <Plus size={16} />
            Создать
          </button>
          {!ready && (
            <button
              className="nav-button"
              disabled={blocking}
              onClick={() => fileInput.current?.click()}
            >
              <FileUp size={16} />
              Открыть .bpmn
            </button>
          )}
          <button
            className="nav-button workspace-action"
            disabled={!ready || blocking || !!pending}
            onClick={check}
          >
            <ShieldCheck size={16} />
            Проверить
          </button>
          <button className="nav-button workspace-action" disabled={!aiReady} onClick={improve}>
            <Workflow size={16} />
            Улучшить
          </button>
          <span className="nav-divider workspace-action" />
          {ready && (
            <button
              className="icon-button"
              aria-label={panelCollapsed ? 'Показать Copilot' : 'Скрыть Copilot'}
              title={panelCollapsed ? 'Показать Copilot' : 'Скрыть Copilot'}
              aria-expanded={!panelCollapsed}
              aria-controls="copilot"
              disabled={blocking || !!pending}
              onClick={() => {
                setPanelCollapsed((v) => !v);
                requestAnimationFrame(() => editor.current?.fit());
              }}
            >
              {panelCollapsed ? <PanelRightOpen size={17} /> : <PanelRightClose size={17} />}
            </button>
          )}
          <button
            className="export workspace-action"
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
          <span className="project-name" title={process?.name}>
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
              <Suspense
                fallback={
                  <div className="bpmn-canvas" role="status" aria-busy="true">
                    Загрузка редактора BPMN…
                  </div>
                }
              >
                <Editor
                  ref={editor}
                  xml={xml}
                  showTools={showTools}
                  onSelect={setSelected}
                  onReady={setEditorReady}
                  onError={setError}
                  onChange={() => {
                    setDirty(true);
                    setAudit(null);
                    setProposal(null);
                    setSelected(null);
                  }}
                />
              </Suspense>
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
                  ? `${process.pools?.length ? process.pools.length + ' пула · ' : ''}${process.participants.length} участников · ${process.nodes.filter((n) => n.type.endsWith('task')).length} действий · ${process.flows.length} переходов${process.message_flows?.length ? ' · ' + process.message_flows.length + ' сообщений' : ''}`
                  : 'Файл BPMN'}
                <span>Перетаскивайте элементы для редактирования</span>
              </div>
            </>
          ) : (
            <div className="start-screen">
              <div className="eyebrow">
                <span />
                МОДЕЛИРОВАНИЕ ПРОЦЕССОВ
              </div>
              <h1>
                От описания
                <br />
                <span>к BPMN-модели.</span>
              </h1>
              <p className="intro">
                Участники, действия и решения — в схеме, которую можно редактировать.
              </p>
              <div className="home-compose">
                <div className="compose-main">
                  <div className="input-sheet">
                    {error && !pending && (
                      <div className="feedback error home-feedback" role="alert">
                        <CircleAlert size={16} />
                        <div>
                          <strong>Не удалось построить процесс</strong>
                          <p>{error}</p>
                        </div>
                        <button aria-label="Скрыть ошибку" onClick={() => setError('')}>
                          <X size={14} />
                        </button>
                      </div>
                    )}
                    <div className="input-heading">
                      <label htmlFor="description">
                        <FileText size={16} />
                        Описание процесса
                      </label>
                      <span>Обычным языком</span>
                    </div>
                    <textarea
                      id="description"
                      value={text}
                      onChange={(e) => setText(e.target.value)}
                      maxLength={20000}
                      disabled={blocking}
                      onKeyDown={(e) => {
                        if (
                          e.key === 'Enter' &&
                          (e.ctrlKey || e.metaKey) &&
                          !blocking &&
                          text.trim().length >= 10
                        ) {
                          e.preventDefault();
                          generate();
                        }
                      }}
                      placeholder="Клиент отправляет заявку. Оператор проверяет документы. Если чего-то не хватает — возвращает на доработку…"
                    />
                    <div className="input-bottom">
                      <div className="example-picker" ref={examplePicker}>
                        <button
                          className="text-button"
                          disabled={blocking || !examples.length}
                          aria-expanded={examplesOpen}
                          aria-controls="example-menu"
                          onClick={() => setExamplesOpen((v) => !v)}
                        >
                          <FileText size={16} />
                          Загрузить пример
                          <ChevronDown size={14} />
                        </button>
                        {examplesOpen && (
                          <div className="example-menu" id="example-menu">
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
                          <LoaderCircle className="spin" size={16} />
                        ) : (
                          <Workflow size={16} />
                        )}
                        Создать BPMN
                      </button>
                    </div>
                  </div>
                  <div className="home-examples">
                    <span>Начните с примера</span>
                    {examples.slice(0, 2).map((e, i) => (
                      <button
                        key={e.name}
                        aria-label={'Использовать пример: ' + e.name}
                        disabled={blocking}
                        onClick={() => sample(i)}
                      >
                        {e.name}
                        <ArrowRight size={13} />
                      </button>
                    ))}
                  </div>
                  <div className="trust-note">
                    <CheckCheck size={15} />
                    Важные детали уточним перед построением{' '}
                    <span className="input-shortcut">Ctrl + Enter — создать</span>
                  </div>
                  <p className="data-note">
                    {health?.provider === 'mock'
                      ? 'Демо на примерах · без обращения к AI-модели'
                      : 'Текст передаётся настроенному AI-провайдеру. Для демо используйте обезличенные данные.'}
                  </p>
                </div>
                <aside className="home-guide">
                  <span className="section-kicker">ПОДСКАЗКИ ДЛЯ ОПИСАНИЯ</span>
                  <h2>Три опорные точки</h2>
                  <ol>
                    <li>
                      <strong>Кто участвует</strong>
                      <p>Назовите роли и организации.</p>
                    </li>
                    <li>
                      <strong>Что происходит</strong>
                      <p>Опишите действия в порядке выполнения.</p>
                    </li>
                    <li>
                      <strong>Какие есть условия</strong>
                      <p>Укажите альтернативы, возвраты и параллельные шаги.</p>
                    </li>
                  </ol>
                  <div className="home-output">
                    <Workflow size={19} />
                    <div>
                      <strong>BPMN 2.0</strong>
                      <span>
                        Открытый формат · <span className="nowrap">Редактируемая схема</span>
                      </span>
                    </div>
                  </div>
                </aside>
              </div>
            </div>
          )}
          {busy && (
            <div className="working" role="status">
              <BrandMark size={30} animated />
              <div>
                <strong>{busy}</strong>
                <span>
                  Результат появится здесь. Можно дождаться ответа, не отправляя запрос повторно.
                </span>
              </div>
            </div>
          )}
        </section>
        <aside className="copilot" id="copilot" aria-label="PULSE Copilot">
          <div className="copilot-heading">
            <span className="copilot-mark">
              <BrandMark size={26} />
            </span>
            <div>
              <strong>PULSE Copilot</strong>
              <span>Анализ и изменения</span>
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
                id={`tab-${id}`}
                aria-controls="copilot-panel"
                tabIndex={panel === id ? 0 : -1}
                aria-selected={panel === id}
                className={panel === id ? 'active' : ''}
                onClick={() => setPanel(id)}
                onKeyDown={(e) => {
                  const ids: Panel[] = ['copilot', 'audit', 'history'];
                  const next =
                    e.key === 'Home'
                      ? 0
                      : e.key === 'End'
                        ? 2
                        : e.key === 'ArrowRight'
                          ? (ids.indexOf(panel) + 1) % 3
                          : e.key === 'ArrowLeft'
                            ? (ids.indexOf(panel) + 2) % 3
                            : -1;
                  if (next < 0) return;
                  e.preventDefault();
                  setPanel(ids[next]);
                  document.getElementById(`tab-${ids[next]}`)?.focus();
                }}
              >
                <Icon size={15} />
                {label}
              </button>
            ))}
          </div>
          <div
            className="panel-content"
            id="copilot-panel"
            role="tabpanel"
            aria-labelledby={`tab-${panel}`}
            tabIndex={0}
            ref={panelContent}
          >
            <div className="provider-info">
              {llmMetadata && llmMetadata.provider_used !== 'mock' ? (
                <div role="status" aria-live="polite">
                  <span className="provider-name">
                    {llmMetadata.fallback_used
                      ? modelLabel(llmMetadata.model_used) + ' · резервная модель'
                      : 'Модель: ' + modelLabel(llmMetadata.model_used)}
                  </span>
                </div>
              ) : !health ? (
                'Подключение к backend…'
              ) : health.provider === 'mock' ? (
                <>
                  <span className="demo-pill">ТЕСТОВЫЙ РЕЖИМ</span>
                  <p>Встроенные примеры · Без обращения к модели</p>
                </>
              ) : (
                <>
                  <span className="provider-name">{health.provider}</span>
                  <p>
                    {health.configured
                      ? 'Готов к работе'
                      : 'Модель не настроена. Обратитесь к администратору.'}
                  </p>
                </>
              )}
            </div>
            {error && (
              <div className="feedback error" role="alert">
                <CircleAlert size={17} />
                <div>
                  <strong>Не удалось выполнить действие</strong>
                  <p>{error}</p>
                </div>
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
                {!!displayedAssumptions.length && (
                  <details className="assumptions-summary">
                    <summary>Приняты допущения: {displayedAssumptions.length}</summary>
                    <ul>
                      {displayedAssumptions.map((a, i) => (
                        <li key={i}>{a.text}</li>
                      ))}
                    </ul>
                  </details>
                )}
                {pending ? (
                  <div className="clarify">
                    <div className="section-kicker">УТОЧНЕНИЕ ПРОЦЕССА</div>
                    <h2>
                      Нужно уточнить {pending.ambiguities.length}{' '}
                      {new Intl.PluralRules('ru').select(pending.ambiguities.length) === 'one'
                        ? 'момент'
                        : new Intl.PluralRules('ru').select(pending.ambiguities.length) === 'few'
                          ? 'момента'
                          : 'моментов'}
                    </h2>
                    <p className="muted">
                      {clarifyContext?.limitReached
                        ? pendingCritical
                          ? 'Лимит уточнений достигнут. Критичные вопросы остаются: измените описание или команду.'
                          : 'Лимит уточнений достигнут. Некритичные вопросы можно принять как допущения.'
                        : 'Подтвердите важные детали перед построением схемы.'}
                    </p>
                    {pending.ambiguities.map((a, i) => (
                      <fieldset key={a.id} data-priority={a.severity}>
                        <legend>
                          <span className={`question-priority ${a.severity}`}>
                            {a.severity === 'critical' ? 'Критично' : 'Желательно уточнить'}
                          </span>
                          {i + 1}. {a.question}
                        </legend>
                        <div className="answer-options">
                          {a.suggested_answers.map((answer) => (
                            <button
                              key={answer}
                              disabled={blocking || acceptedQuestions.includes(a.id)}
                              className={answers[a.id] === answer ? 'chosen' : ''}
                              aria-pressed={answers[a.id] === answer}
                              onClick={() => {
                                setAnswers((v) => ({ ...v, [a.id]: answer }));
                                setAcceptedQuestions((v) => v.filter((id) => id !== a.id));
                              }}
                            >
                              <span className="answer-check" aria-hidden="true">
                                {answers[a.id] === answer && <Check size={13} />}
                              </span>
                              {answer}
                            </button>
                          ))}
                        </div>
                        {a.allow_custom_answer !== false && (
                          <input
                            aria-label={'Свой ответ: ' + a.question}
                            placeholder="Или свой ответ…"
                            value={
                              a.suggested_answers.includes(answers[a.id]) ? '' : answers[a.id] || ''
                            }
                            onChange={(e) => {
                              setAnswers((v) => ({ ...v, [a.id]: e.target.value }));
                              setAcceptedQuestions((v) => v.filter((id) => id !== a.id));
                            }}
                            disabled={blocking || acceptedQuestions.includes(a.id)}
                          />
                        )}
                        {a.severity === 'warning' && (
                          <label className="assumption-choice">
                            <input
                              type="checkbox"
                              checked={acceptedQuestions.includes(a.id)}
                              disabled={blocking}
                              onChange={(e) => {
                                setAcceptedQuestions((v) =>
                                  e.target.checked ? [...v, a.id] : v.filter((id) => id !== a.id),
                                );
                                if (e.target.checked) setAnswers((v) => ({ ...v, [a.id]: '' }));
                              }}
                            />
                            Принять как допущение
                          </label>
                        )}
                        {a.severity === 'warning' && a.assumption && (
                          <p className="assumption-text">{a.assumption}</p>
                        )}
                      </fieldset>
                    ))}
                    <button
                      className="primary full"
                      disabled={
                        blocking ||
                        (clarifyContext?.limitReached && pendingCritical) ||
                        (!clarifyContext?.limitReached &&
                          pending.ambiguities.some(
                            (a) => !answers[a.id]?.trim() && !acceptedQuestions.includes(a.id),
                          ))
                      }
                      onClick={() =>
                        void run('PULSE уточняет процесс…', async () => {
                          const result = await api<Result>('process/clarify', {
                            process: preflight ? null : pending,
                            preflight,
                            answers: Object.fromEntries(
                              Object.entries(answers).filter(
                                ([id]) => !acceptedQuestions.includes(id),
                              ),
                            ),
                            accepted_ambiguity_ids: acceptedQuestions,
                            accepted_assumptions:
                              clarifyContext?.acceptedAssumptions ?? assumptions,
                            original_text: clarifyContext?.originalText || text,
                            operation: clarifyContext?.operation || 'generate',
                            instruction: clarifyContext?.instruction || '',
                            base_process: clarifyContext?.before?.process || null,
                            clarification_round: clarifyContext?.round || 0,
                            continue_with_draft: !!clarifyContext?.limitReached && !pendingCritical,
                          });
                          setClarifyContext((c) =>
                            c
                              ? {
                                  ...c,
                                  round: result.clarification_round ?? c.round + 1,
                                  limitReached: result.clarification_limit_reached ?? false,
                                  acceptedAssumptions:
                                    result.accepted_assumptions ?? c.acceptedAssumptions,
                                }
                              : c,
                          );
                          if (clarifyContext?.proposal && result.xml && result.process) {
                            await checkImport(result.xml);
                            setProposal(result);
                            setLLMMetadata(result.metadata ?? null);
                            setPending(null);
                            setPreflight(undefined);
                            setClarifyContext(null);
                            setPanel('copilot');
                          } else if (
                            clarifyContext?.operation === 'modify' &&
                            clarifyContext.before
                          ) {
                            await prepareChange(
                              result,
                              clarifyContext.before,
                              clarifyContext.instruction,
                            );
                          } else {
                            await accept(result, 'Ответы учтены. BPMN построен.');
                          }
                        })
                      }
                    >
                      {clarifyContext?.limitReached && !pendingCritical
                        ? 'Продолжить с допущениями черновика'
                        : 'Продолжить'}
                      <ArrowRight size={15} />
                    </button>
                    <button
                      className="text-button cancel"
                      disabled={blocking}
                      onClick={() => {
                        setPending(null);
                        setPreflight(undefined);
                        if (clarifyContext?.limitReached) {
                          if (clarifyContext.operation === 'modify')
                            setCommand(clarifyContext.instruction);
                          else if (ready) setInputOpen(true);
                        }
                        setClarifyContext(null);
                        setAnswers({});
                      }}
                    >
                      {clarifyContext?.limitReached
                        ? clarifyContext.operation === 'modify'
                          ? 'Изменить команду'
                          : 'Изменить описание'
                        : 'Отменить уточнение'}
                    </button>
                  </div>
                ) : (
                  <>
                    {editPreview && (
                      <ChangePreview
                        result={editPreview.result}
                        nextVersion={version + 1}
                        instruction={editPreview.instruction}
                        busy={blocking}
                        onApply={applyPreview}
                        onCancel={() => {
                          setCommand(editPreview.instruction);
                          setEditPreview(null);
                          setNotice('Предпросмотр отменён. Текущая версия сохранена.');
                        }}
                      />
                    )}
                    {!editPreview && changes.length > 0 && (
                      <section className="change-summary" aria-label="Изменения процесса">
                        <h2>Процесс обновлён</h2>
                        <button
                          className="text-button"
                          disabled={blocking || !versions.length}
                          onClick={() => restore(versions.length - 1)}
                        >
                          <Undo2 size={15} />
                          Отменить AI-изменение
                        </button>
                        <ChangeList changes={changes} />
                      </section>
                    )}
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
                            <span>{proposal.process!.nodes.length} элементов</span>
                            <span>{proposal.process!.flows.length} связей</span>
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
                    {!editPreview &&
                      (node && !dirty ? (
                        <div className="evidence">
                          <div className="section-kicker">ИСТОЧНИК ЭЛЕМЕНТА</div>
                          <h2>{node.name}</h2>
                          <dl>
                            <dt>Участник</dt>
                            <dd>
                              {
                                process?.participants.find((p) => p.id === node.participant_id)
                                  ?.name
                              }
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
                          <h2>Работайте со схемой</h2>
                          <p>
                            {dirty
                              ? 'Схема изменена вручную. Перед AI-операцией текущие элементы и связи будут синхронизированы с моделью.'
                              : 'Выберите элемент на полотне, чтобы увидеть участника и источник в описании.'}
                          </p>
                          {process && (
                            <dl className="model-facts">
                              <div>
                                <dt>Участники</dt>
                                <dd>{process.participants.length}</dd>
                              </div>
                              <div>
                                <dt>Действия</dt>
                                <dd>
                                  {process.nodes.filter((n) => n.type.endsWith('task')).length}
                                </dd>
                              </div>
                              <div>
                                <dt>Версия</dt>
                                <dd>v{version || 1}</dd>
                              </div>
                            </dl>
                          )}
                          <div className="assistant-tip">
                            <MessageSquare size={16} />
                            <p>
                              Добавить шаг или изменить порядок? Опишите команду ниже — сначала
                              покажем изменения.
                            </p>
                          </div>
                        </div>
                      ) : null)}
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
                    <h3>Замечания и уточнения</h3>
                    <p className="muted">
                      {audit.llm_audit
                        ? 'Программные правила + рекомендации модели'
                        : 'Программные правила. Аудит модели не выполнялся.'}
                    </p>
                    {audit.notice && <p className="muted">{audit.notice}</p>}
                    <div className="issues">
                      {!audit.issues.length && (
                        <div className="empty-panel">
                          <CheckCheck size={24} />
                          <p>По результатам проверки замечаний нет.</p>
                        </div>
                      )}
                      {audit.issues.map((issue, i) => (
                        <Finding
                          key={i}
                          issue={issue}
                          process={process}
                          onLocate={() => editor.current?.highlight(issue.node_ids)}
                        />
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
              <HistoryPanel
                ready={ready}
                version={version}
                description={versionDescription}
                processName={process?.name}
                versions={versions}
                disabled={blocking}
                onRestore={restore}
              />
            )}
          </div>
          {ready && panel === 'copilot' && !pending && !editPreview && (
            <div className="command-area">
              <label htmlFor="command">Что хотите изменить?</label>
              <div className="command-box">
                <textarea
                  id="command"
                  value={command}
                  onChange={(e) => setCommand(e.target.value)}
                  disabled={!aiReady}
                  maxLength={4000}
                  placeholder={
                    ready
                      ? 'Например: сделай проверки юриста и службы безопасности параллельными'
                      : 'Сначала создайте BPMN-модель'
                  }
                  onKeyDown={(e) => {
                    if (
                      e.key === 'Enter' &&
                      (e.ctrlKey || e.metaKey) &&
                      aiReady &&
                      command.trim().length > 2
                    ) {
                      e.preventDefault();
                      modify();
                    }
                  }}
                />
                <button
                  className="send"
                  aria-label="Подготовить изменения"
                  onClick={modify}
                  disabled={!aiReady || command.trim().length < 3}
                >
                  <span>Подготовить изменения</span>
                  {blocking ? <LoaderCircle className="spin" size={17} /> : <Send size={17} />}
                </button>
              </div>
              {aiReady && process?.nodes.some((n) => n.name === 'Проверить документы') && (
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
                  : 'После создания схемы здесь можно описать изменение'}
              </span>
            </div>
          )}
        </aside>
      </main>
      {inputOpen && (
        <div
          className="modal-backdrop"
          onClick={(e) => {
            if (e.target === e.currentTarget && !blocking) setInputOpen(false);
          }}
        >
          <section
            className="input-modal"
            ref={modal}
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
              {blocking ? <LoaderCircle className="spin" size={17} /> : <Workflow size={17} />}
              Создать BPMN
            </button>
          </section>
        </div>
      )}
    </div>
  );
}
