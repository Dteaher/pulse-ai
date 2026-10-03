import { useState } from 'react';
import type { Process, Result, Snapshot } from '../types';
import { applyProcess, initialProcessState, undoProcess, loadProcess } from './processState';

export function useProcessState() {
  const [state, setState] = useState(initialProcessState);
  return {
    ...state,
    reset: () => setState(initialProcessState),
    load: (process: Process | null, xml: string, before?: Snapshot) =>
      setState((s) => loadProcess(s, process, xml, before)),
    apply: (result: Result, before?: Snapshot, description?: string) =>
      setState((s) => applyProcess(s, result, before, description)),
    undo: (index?: number) => setState((s) => undoProcess(s, index)),
  };
}
