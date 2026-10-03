import type { Ambiguity, Change, Process, Result, Snapshot } from '../types';

export interface ProcessState {
  process: Process | null;
  xml: string;
  versions: Snapshot[];
  changes: Change[];
  assumptions: Ambiguity[];
  version: number;
  description: string;
}
export const initialProcessState: ProcessState = {
  process: null,
  xml: '',
  versions: [],
  changes: [],
  assumptions: [],
  version: 0,
  description: '',
};

export function applyProcess(
  state: ProcessState,
  result: Result,
  before?: Snapshot,
  description = 'Создан по описанию',
): ProcessState {
  if (!result.xml || !result.process) return state;
  return {
    process: result.process,
    xml: result.xml,
    changes: result.changes ?? [],
    assumptions: result.accepted_assumptions ?? (before ? state.assumptions : []),
    version: Math.max(state.version, before ? 1 : 0) + 1,
    description,
    versions: before
      ? [
          ...state.versions.slice(-19),
          structuredClone({
            ...before,
            version: before.version ?? (state.version || 1),
            assumptions: before.assumptions ?? state.assumptions,
            changes: before.changes ?? state.changes,
          }),
        ]
      : state.versions,
  };
}

export function undoProcess(state: ProcessState, index = state.versions.length - 1): ProcessState {
  const snapshot = state.versions[index];
  if (!snapshot) return state;
  return {
    process: structuredClone(snapshot.process),
    xml: snapshot.xml,
    versions: state.versions.slice(0, index),
    assumptions: structuredClone(snapshot.assumptions ?? []),
    version: snapshot.version ?? index + 1,
    description: snapshot.label,
    changes: structuredClone(snapshot.changes ?? []),
  };
}

export function loadProcess(
  state: ProcessState,
  process: Process | null,
  xml: string,
  before?: Snapshot,
): ProcessState {
  return {
    process,
    xml,
    changes: [],
    assumptions: [],
    version: Math.max(state.version, before ? 1 : 0) + 1,
    description: 'Импортирован BPMN-файл',
    versions: before
      ? [
          ...state.versions.slice(-19),
          structuredClone({
            ...before,
            version: state.version || 1,
            assumptions: state.assumptions,
            changes: state.changes,
          }),
        ]
      : state.versions,
  };
}
