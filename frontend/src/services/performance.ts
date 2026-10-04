/** Local measurements only; no process content or credentials leave the browser. */
export function recordBpmnImport(start: number, phase: 'check' | 'render') {
  const name = `pulse.frontend_bpmn_import.${phase}`;
  performance.clearMeasures(name);
  performance.measure(name, { start, end: performance.now() });
}

let session: string = crypto.randomUUID();
try {
  session = sessionStorage.getItem('pulse.requestScope') || session;
  sessionStorage.setItem('pulse.requestScope', session);
} catch {
  // A storage-disabled browser retains an isolated in-memory scope for this tab.
}
export const requestScope = session;
