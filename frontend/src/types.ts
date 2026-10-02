export interface Participant {
  id: string;
  name: string;
  type: string;
}
export interface Node {
  id: string;
  name: string;
  type: string;
  participant_id: string;
  source_text: string;
  confidence: 'high' | 'medium' | 'confirmation_required';
  inferred: boolean;
}
export interface Flow {
  id: string;
  source: string;
  target: string;
  name: string;
  condition: string | null;
  is_default: boolean;
}
export interface Ambiguity {
  id: string;
  question: string;
  related_node_ids: string[];
  severity: string;
  suggested_answers: string[];
}
export interface Process {
  id: string;
  name: string;
  description: string;
  participants: Participant[];
  nodes: Node[];
  flows: Flow[];
  ambiguities: Ambiguity[];
}
export interface LLMMetadata {
  provider_used: string;
  model_used: string;
  fallback_used: boolean;
  attempts?: number;
}
export interface Result {
  process: Process;
  ambiguities: Ambiguity[];
  xml: string | null;
  attempts: number;
  metadata?: LLMMetadata;
}
export interface Issue {
  severity: 'error' | 'warning' | 'info';
  message: string;
  node_ids: string[];
  origin: string;
}
export interface Audit {
  technical: Record<string, boolean>;
  issues: Issue[];
  llm_audit: boolean;
  notice: string;
  metadata?: LLMMetadata | null;
}
export interface Snapshot {
  xml: string;
  process: Process | null;
  label: string;
}
