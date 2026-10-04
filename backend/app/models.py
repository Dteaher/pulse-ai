from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, AliasChoices, field_validator, model_validator

Identifier = str
NodeType = Literal['start_event', 'end_event', 'task', 'user_task', 'service_task',
                   'script_task', 'send_task', 'receive_task', 'exclusive_gateway', 'parallel_gateway',
                   'inclusive_gateway', 'event_based_gateway', 'intermediate_event']


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Participant(StrictModel):
    id: Identifier = Field(pattern=r'^[A-Za-z_][A-Za-z0-9_.-]*$', max_length=100)
    name: str = Field(min_length=1, max_length=200)
    type: Literal['role', 'department', 'system', 'external'] = 'role'
    kind: Literal['internal', 'external'] = 'internal'
    pool_id: Identifier | None = None


class Pool(StrictModel):
    id: Identifier = Field(pattern=r'^[A-Za-z_][A-Za-z0-9_.-]*$', max_length=100)
    name: str = Field(min_length=1, max_length=200)
    kind: Literal['internal', 'external'] = 'internal'


class MessageFlow(StrictModel):
    id: Identifier = Field(pattern=r'^[A-Za-z_][A-Za-z0-9_.-]*$', max_length=100)
    source: Identifier
    target: Identifier
    name: str = Field(default='', max_length=200)


class Node(StrictModel):
    id: Identifier = Field(pattern=r'^[A-Za-z_][A-Za-z0-9_.-]*$', max_length=100)
    type: NodeType
    event_definition: Literal['none', 'message'] = 'none'
    decision_basis: Literal['unspecified', 'data', 'event'] = 'unspecified'
    name: str = Field(default='', max_length=300)
    participant_id: Identifier
    source_text: str = Field(default='', max_length=2000)
    confidence: Literal['high', 'medium', 'confirmation_required'] = 'medium'
    inferred: bool = False


class Flow(StrictModel):
    id: Identifier = Field(pattern=r'^[A-Za-z_][A-Za-z0-9_.-]*$', max_length=100)
    source: Identifier
    target: Identifier
    name: str = Field(default='', max_length=200)
    condition: str | None = Field(default=None, max_length=500)
    is_default: bool = False


class Ambiguity(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    question: str = Field(min_length=1, max_length=600)
    type: Literal['missing_branch', 'unclear_parallelism', 'unclear_sequence', 'unclear_participant', 'unclear_result', 'unclear_end', 'unclear_condition', 'other'] = 'other'
    related_node_ids: list[str] = Field(default_factory=list, max_length=20)
    severity: Literal['critical', 'warning'] = 'critical'
    suggested_answers: list[str] = Field(default_factory=list, max_length=5)
    allow_custom_answer: bool = True
    assumption: str = Field(default='', max_length=600)
    reason: str = Field(default='', max_length=600)
    source_excerpt: str = Field(default='', max_length=2000)
    related_participants: list[str] = Field(default_factory=list, max_length=30)


class Assumption(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=600)
    source: Literal['model_inference', 'user_accepted'] = 'model_inference'
    confidence: float = Field(default=0.5, ge=0, le=1)


class AmbiguityAnalysis(StrictModel):
    ambiguities: list[Ambiguity] = Field(default_factory=list, max_length=3)
    assumptions: list[Assumption] = Field(default_factory=list, max_length=30)
    superseded_assumption_ids: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode='after')
    def unique_questions(self):
        if len({q.id for q in self.ambiguities}) != len(self.ambiguities):
            raise ValueError('Ambiguity IDs must be unique.')
        return self



class PreflightState(StrictModel):
    original_text: str = Field(min_length=1, max_length=20000)
    analysis: AmbiguityAnalysis
    answers: dict[str, str] = Field(default_factory=dict, max_length=30)


class ProcessDefinition(StrictModel):
    id: Identifier = Field(pattern=r'^[A-Za-z_][A-Za-z0-9_.-]*$', max_length=100)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default='', max_length=20000)
    participants: list[Participant] = Field(min_length=1, max_length=30)
    nodes: list[Node] = Field(min_length=2, max_length=150)
    flows: list[Flow] = Field(min_length=1, max_length=300)
    pools: list[Pool] = Field(default_factory=list, max_length=30)
    message_flows: list[MessageFlow] = Field(default_factory=list, max_length=100)
    ambiguities: list[Ambiguity] = Field(default_factory=list, max_length=3)
    assumptions: list[Assumption] = Field(default_factory=list, max_length=30)


class Issue(StrictModel):
    code: str = 'VALIDATION_ISSUE'
    node_id: str | None = None
    process_id: str | None = None
    flow_id: str | None = None
    gateway_id: str | None = None
    source_pool: str | None = None
    target_pool: str | None = None
    severity: Literal['error', 'warning', 'clarification', 'info']
    source: Literal['BPMN_SPEC', 'BUSINESS_LOGIC', 'MODEL_QUALITY', 'LAYOUT', 'XSD'] = 'MODEL_QUALITY'
    spec_section: str | None = None
    line: int | None = None
    column: int | None = None
    type: Literal['SPEC_ERROR', 'MODEL_ERROR', 'CLARIFICATION_REQUIRED', 'INTEROPERABILITY_ERROR', 'LAYOUT_WARNING', 'BUSINESS_WARNING'] | None = None
    category: str = ''
    suggested_fix: str = ''
    message: str = Field(min_length=1, max_length=1000)
    node_ids: list[str] = Field(default_factory=list, max_length=150)
    origin: Literal['rule', 'llm'] = 'rule'

    @model_validator(mode='after')
    def diagnostic_classification(self):
        if self.type is None:
            self.type = ('CLARIFICATION_REQUIRED' if self.severity == 'clarification' else
                         'INTEROPERABILITY_ERROR' if self.source == 'XSD' or 'DI_' in self.code or self.code == 'OUTSIDE_PULSE_SUBSET' else
                         'LAYOUT_WARNING' if self.source == 'LAYOUT' else
                         'BUSINESS_WARNING' if self.source == 'BUSINESS_LOGIC' and self.severity != 'error' else
                         'SPEC_ERROR' if self.source == 'BPMN_SPEC' else 'MODEL_ERROR')
        if not self.category:
            self.category = ('gateway' if 'GATEWAY' in self.code else
                             'message_flow' if 'MESSAGE_FLOW' in self.code else
                             'sequence_flow' if 'SEQUENCE_FLOW' in self.code else
                             'reference' if 'REFERENCE' in self.code or 'DUPLICATE' in self.code else
                             'reachability' if self.code in {'ORPHAN_NODE','UNREACHABLE_NODE','DEAD_END','NO_PATH_TO_END'} else
                             'business_information' if self.type == 'CLARIFICATION_REQUIRED' else
                             self.source.lower())
        self.suggested_fix = self.suggested_fix or ('Уточните бизнес-правило.' if self.type in {'CLARIFICATION_REQUIRED','BUSINESS_WARNING'} else 'Проверьте указанные элементы и их связи.')
        return self



class AuditResult(StrictModel):
    issues: list[Issue] = Field(default_factory=list, max_length=20)


class LLMMetadata(StrictModel):
    provider_used: str
    model_used: str
    fallback_used: bool = False
    attempts: int = Field(default=1, ge=0)


class ClarificationResult(StrictModel):
    process: ProcessDefinition


class ModificationResult(StrictModel):
    process: ProcessDefinition


class ProcessPreparation(StrictModel):
    """Strict gate: questions and a completed process are mutually exclusive."""
    status: Literal['ready', 'clarification_required']
    analysis: AmbiguityAnalysis
    process: ProcessDefinition | None

    @model_validator(mode='after')
    def gate(self):
        critical = any(q.severity == 'critical' for q in self.analysis.ambiguities)
        if self.status == 'clarification_required':
            if not critical or self.process is not None:
                raise ValueError('Критические вопросы требуют process=null.')
        elif critical or self.process is None or any(q.severity == 'critical' for q in self.process.ambiguities):
            raise ValueError('Готовый процесс не должен содержать критические вопросы.')
        return self


class ProcessPatch(StrictModel):
    """Provider-neutral edits; canonical ProcessDefinition never changes format."""
    nodes: list[Node] = Field(default_factory=list, max_length=150)
    flows: list[Flow] = Field(default_factory=list, max_length=300)
    participants: list[Participant] = Field(default_factory=list, max_length=30)
    pools: list[Pool] = Field(default_factory=list, max_length=30)
    message_flows: list[MessageFlow] = Field(default_factory=list, max_length=100)
    assumptions: list[Assumption] = Field(default_factory=list, max_length=30)
    remove_node_ids: list[str] = Field(default_factory=list, max_length=150)
    remove_flow_ids: list[str] = Field(default_factory=list, max_length=300)
    remove_participant_ids: list[str] = Field(default_factory=list, max_length=30)
    remove_pool_ids: list[str] = Field(default_factory=list, max_length=30)
    remove_message_flow_ids: list[str] = Field(default_factory=list, max_length=100)
    remove_assumption_ids: list[str] = Field(default_factory=list, max_length=30)
    ambiguities: list[Ambiguity] = Field(default_factory=list, max_length=3)
    name: str | None = Field(default=None, min_length=1, max_length=300)

    def apply(self, process: ProcessDefinition, *, allow_business_removal=True) -> ProcessDefinition:
        data = process.model_dump()
        groups = ('nodes', 'flows', 'participants', 'pools', 'message_flows', 'assumptions')
        remove_fields = ('remove_node_ids', 'remove_flow_ids', 'remove_participant_ids', 'remove_pool_ids', 'remove_message_flow_ids', 'remove_assumption_ids')
        if not allow_business_removal:
            protected = {n.id for n in process.nodes if n.type.endswith('task') and (n.source_text or not n.inferred)}
            if protected & set(self.remove_node_ids):
                raise ValueError('Corrective не может удалять подтверждённые бизнес-действия.')
        if any(q.severity == 'critical' for q in self.ambiguities):
            if self.name is not None or any(getattr(self, key) for key in groups + remove_fields):
                raise ValueError('При критичном вопросе изменение графа запрещено.')
            data['ambiguities'] = [q.model_dump() for q in self.ambiguities]
            return ProcessDefinition.model_validate(data)
        for group, remove_field in zip(groups, remove_fields):
            existing = {item['id']: item for item in data[group]}
            removals = getattr(self, remove_field)
            updates = getattr(self, group)
            if len(set(removals)) != len(removals) or not set(removals) <= set(existing):
                raise ValueError('Удаление требует существующих уникальных ID.')
            if len({item.id for item in updates}) != len(updates) or set(removals) & {item.id for item in updates}:
                raise ValueError('Противоречивые изменения одного ID.')
            for key in removals:
                del existing[key]
            existing.update({item.id: item.model_dump() for item in updates})
            data[group] = list(existing.values())
        data['ambiguities'] = [q.model_dump() for q in self.ambiguities]
        if self.name is not None:
            data['name'] = self.name
        return ProcessDefinition.model_validate(data)


class ModificationPreparation(StrictModel):
    status: Literal['ready', 'clarification_required']
    analysis: AmbiguityAnalysis
    patch: ProcessPatch | None

    @model_validator(mode='after')
    def gate(self):
        critical = any(q.severity == 'critical' for q in self.analysis.ambiguities)
        if self.status == 'clarification_required':
            if not critical or self.patch is not None:
                raise ValueError('Критические вопросы требуют patch=null.')
        elif critical or self.patch is None:
            raise ValueError('Готовое изменение требует patch без критических вопросов.')
        return self

    def apply(self, process):
        return ProcessPreparation(status=self.status, analysis=self.analysis,
            process=self.patch.apply(process) if self.patch is not None else None)


class Change(StrictModel):
    type: Literal['node_added', 'node_removed', 'node_changed', 'flow_added', 'flow_removed', 'flow_changed', 'gateway_added', 'participant_changed', 'condition_changed', 'sequence_changed', 'other']
    element_ids: list[str]
    description: str
    category: Literal['structure', 'condition', 'participant', 'branching'] = 'structure'
    action: Literal['added', 'removed', 'changed'] = 'changed'


class ChangeSet(StrictModel):
    changes: list[Change] = Field(default_factory=list)


class GenerateRequest(StrictModel):
    text: str = Field(min_length=10, max_length=20000)


class ProcessRequest(StrictModel):
    process: ProcessDefinition


class ClarifyRequest(ProcessRequest):
    process: ProcessDefinition | None = None
    preflight: PreflightState | None = None
    answers: dict[str, str]
    original_text: str = Field(default='', max_length=20000)
    clarification_round: int = Field(default=0, ge=0)
    operation: Literal['generate', 'modify'] = 'generate'
    instruction: str = Field(default='', max_length=4000)
    base_process: ProcessDefinition | None = None
    continue_with_draft: bool = False
    accepted_ambiguity_ids: list[str] = Field(default_factory=list, max_length=3)
    accepted_assumptions: list[Ambiguity] = Field(default_factory=list, max_length=30)

    @field_validator('accepted_assumptions')
    @classmethod
    def only_optional_assumptions(cls, values):
        if any(a.severity != 'warning' for a in values):
            raise ValueError('Критичные вопросы нельзя принять как допущение.')
        return values

    @field_validator('answers', mode='before')
    @classmethod
    def answer_list(cls, value):
        if isinstance(value, list):
            if any(not isinstance(a, dict) or set(a) != {'ambiguity_id', 'answer'} or not isinstance(a['ambiguity_id'], str) or not isinstance(a['answer'], str) for a in value):
                raise ValueError('Некорректный ответ на вопрос.')
            ids = [a['ambiguity_id'] for a in value]
            if len(ids) != len(set(ids)):
                raise ValueError('Повторяющиеся ответы.')
            return {a['ambiguity_id']: a['answer'] for a in value}
        return value


class ModifyRequest(ProcessRequest):
    command: str = Field(min_length=3, max_length=4000, validation_alias=AliasChoices('instruction', 'command'))
    accepted_assumptions: list[Ambiguity] = Field(default_factory=list, max_length=30)

    @field_validator('accepted_assumptions')
    @classmethod
    def only_optional_assumptions(cls, values):
        return ClarifyRequest.only_optional_assumptions(values)


class ImportRequest(StrictModel):
    xml: str = Field(min_length=1, max_length=2_000_000)
    previous: ProcessDefinition | None = None
