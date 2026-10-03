export interface Participant {
  id: string;
  name: string;
  type: string;
  kind?: 'internal' | 'external';
  pool_id?: string | null;
}
export interface Node {
  id: string;
  name: string;
  type: string;
  participant_id: string;
  event_definition?: 'none' | 'message';
  decision_basis?: 'unspecified' | 'data' | 'event';
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
  type?: string;
  allow_custom_answer?: boolean;
  assumption?: string;
}
export interface Assumption {
  id: string;
  text: string;
  source: 'model_inference' | 'user_accepted';
  confidence: number;
}
export interface PreflightState {
  original_text: string;
  analysis: {
    ambiguities: Ambiguity[];
    assumptions: Assumption[];
    superseded_assumption_ids: string[];
  };
  answers: Record<string, string>;
}
export interface Process {
  assumptions?: Assumption[];
  id: string;
  name: string;
  description: string;
  participants: Participant[];
  nodes: Node[];
  flows: Flow[];
  pools?: { id: string; name: string; kind: 'internal' | 'external' }[];
  message_flows?: { id: string; source: string; target: string; name: string }[];
  ambiguities: Ambiguity[];
}
export interface LLMMetadata {
  provider_used: string;
  model_used: string;
  fallback_used: boolean;
  attempts?: number;
}
export interface Result {
  preflight?: PreflightState;
  process: Process | null;
  ambiguities: Ambiguity[];
  xml: string | null;
  attempts: number;
  metadata?: LLMMetadata | null;
  changes?: Change[];
  clarification_round?: number;
  clarification_limit_reached?: boolean;
  accepted_assumptions?: Ambiguity[];
  semantic_warnings?: Issue[];
}
export interface Change {
  type: string;
  element_ids: string[];
  description: string;
  category?: 'structure' | 'condition' | 'participant' | 'branching';
  action?: 'added' | 'removed' | 'changed';
}
export interface Issue {
  code?: string;
  node_id?: string | null;
  process_id?: string | null;
  flow_id?: string | null;
  gateway_id?: string | null;
  source_pool?: string | null;
  target_pool?: string | null;
  severity: 'error' | 'warning' | 'clarification' | 'info';
  source?: 'BPMN_SPEC' | 'BUSINESS_LOGIC' | 'MODEL_QUALITY' | 'LAYOUT' | 'XSD';
  spec_section?: string | null;
  line?: number | null;
  column?: number | null;
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
  timestamp: string;
  reason: string;
  version?: number;
  assumptions?: Ambiguity[];
  changes?: Change[];
}
