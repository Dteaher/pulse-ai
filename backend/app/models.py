from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

Identifier = str
NodeType = Literal['start_event', 'end_event', 'task', 'user_task', 'service_task',
                   'script_task', 'exclusive_gateway', 'parallel_gateway',
                   'inclusive_gateway', 'intermediate_event']


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Participant(StrictModel):
    id: Identifier = Field(pattern=r'^[A-Za-z_][A-Za-z0-9_.-]*$', max_length=100)
    name: str = Field(min_length=1, max_length=200)
    type: Literal['role', 'department', 'system', 'external'] = 'role'


class Node(StrictModel):
    id: Identifier = Field(pattern=r'^[A-Za-z_][A-Za-z0-9_.-]*$', max_length=100)
    type: NodeType
    name: str = Field(min_length=1, max_length=300)
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
    related_node_ids: list[str] = Field(default_factory=list, max_length=20)
    severity: Literal['critical', 'warning'] = 'critical'
    suggested_answers: list[str] = Field(default_factory=list, max_length=5)


class ProcessDefinition(StrictModel):
    id: Identifier = Field(pattern=r'^[A-Za-z_][A-Za-z0-9_.-]*$', max_length=100)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default='', max_length=20000)
    participants: list[Participant] = Field(min_length=1, max_length=30)
    nodes: list[Node] = Field(min_length=2, max_length=150)
    flows: list[Flow] = Field(min_length=1, max_length=300)
    ambiguities: list[Ambiguity] = Field(default_factory=list, max_length=4)


class Issue(StrictModel):
    severity: Literal['error', 'warning', 'info']
    message: str = Field(min_length=1, max_length=1000)
    node_ids: list[str] = Field(default_factory=list, max_length=150)
    origin: Literal['rule', 'llm'] = 'rule'


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


class GenerateRequest(StrictModel):
    text: str = Field(min_length=10, max_length=20000)


class ProcessRequest(StrictModel):
    process: ProcessDefinition


class ClarifyRequest(ProcessRequest):
    answers: dict[str, str]


class ModifyRequest(ProcessRequest):
    command: str = Field(min_length=3, max_length=4000)


class ImportRequest(StrictModel):
    xml: str = Field(min_length=1, max_length=2_000_000)
    previous: ProcessDefinition | None = None
