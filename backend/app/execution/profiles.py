"""Contract for a later user-selected engine. No concrete profile is installed."""
from typing import Protocol
from .models import ConditionBinding, VariableDefinition, TaskBinding
from ..models import Issue, Node


class EngineProfile(Protocol):
    name: str
    expression_language: str

    def validate_condition(self, binding: ConditionBinding,
                           variables: dict[str, VariableDefinition]) -> list[Issue]: ...

    def validate_task(self, node: Node, binding: TaskBinding | None) -> list[Issue]: ...

# Engine-specific implementations and their tests require a separate decision.
# This contract deliberately has no deployment client or isExecutable switch.
