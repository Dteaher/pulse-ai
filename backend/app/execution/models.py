from typing import Literal
import re
from pydantic import Field, field_validator
from ..models import StrictModel, ProcessDefinition, Issue


class VariableDefinition(StrictModel):
    type: Literal['string', 'integer', 'number', 'boolean', 'date', 'object', 'array']
    description: str = Field(default='', max_length=1000)


class ConditionBinding(StrictModel):
    expression: str = Field(min_length=1, max_length=2000)
    language: str = Field(min_length=1, max_length=100)
    referenced_variables: list[str] = Field(default_factory=list, max_length=100)


class TaskBinding(StrictModel):
    implementation: str | None = Field(default=None, min_length=1, max_length=1000)
    assignee: str | None = Field(default=None, min_length=1, max_length=200)
    candidate_groups: list[str] = Field(default_factory=list, max_length=30)
    integration: str | None = Field(default=None, min_length=1, max_length=1000)


class MessageBinding(StrictModel):
    message_name: str = Field(min_length=1, max_length=200)
    correlation_mode: Literal['new_instance', 'existing_instance', 'unresolved'] = 'unresolved'
    correlation_variables: list[str] = Field(default_factory=list, max_length=30)


class ExecutionReadinessRequest(StrictModel):
    process: ProcessDefinition
    variables: dict[str, VariableDefinition] = Field(default_factory=dict, max_length=100)
    conditions: dict[str, ConditionBinding] = Field(default_factory=dict, max_length=300)
    tasks: dict[str, TaskBinding] = Field(default_factory=dict, max_length=150)
    messages: dict[str, MessageBinding] = Field(default_factory=dict, max_length=100)
    # Explicitly user-selected internal pool scope. External pools stay descriptive.
    executable_pool_ids: list[str] = Field(default_factory=list, max_length=30)
    error_policy_element_ids: list[str] = Field(default_factory=list, max_length=150)
    timeout_policy_element_ids: list[str] = Field(default_factory=list, max_length=150)

    @field_validator('variables')
    @classmethod
    def variable_names(cls, value):
        if any(not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name) for name in value):
            raise ValueError('Variable dictionary requires explicit simple identifiers.')
        return value


class ExecutionReadinessResult(StrictModel):
    status: Literal['requires_configuration'] = 'requires_configuration'
    engine_selected: bool = False
    engine: str | None = None
    execution_verified: bool = False
    deployable: bool = False
    supplied_configuration_verified: bool = False
    issues: list[Issue]
