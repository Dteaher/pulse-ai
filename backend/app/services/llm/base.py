from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal
from ...models import ProcessDefinition, ClarificationResult, ModificationResult, AuditResult, LLMMetadata


from .exceptions import ProviderError


@dataclass(frozen=True)
class LLMMessage:
    role: Literal['system', 'user', 'assistant']
    content: str


class LLMProvider(ABC):
    name: str = ''
    model: str = ''
    business_audit_available: bool = True
    attempt_count: int = 0

    @abstractmethod
    async def analyze_ambiguities(self, text: str, context: dict | None = None):
        """Analyze business information without constructing a graph."""
        ...

    @property
    def configured(self):
        return not self.missing_settings()

    def missing_settings(self) -> list[str]:
        return []

    def new_request(self):
        return self

    def use_validation_fallback(self) -> bool:
        """Optional failover only after the pipeline exhausts corrective retries."""
        return False

    @property
    def connection_info(self):
        return {'primary': {'provider': self.name, 'model': self.model},
                'fallback': {'enabled': False, 'provider': '', 'model': ''}}

    @property
    def metadata(self) -> LLMMetadata:
        return LLMMetadata(provider_used=self.name, model_used=self.model, attempts=self.attempt_count)

    @abstractmethod
    async def parse_process(self, text: str, correction: str = '') -> ProcessDefinition: ...

    @abstractmethod
    async def clarify_process(self, process: ProcessDefinition, answers: dict[str, str], correction: str = '') -> ClarificationResult: ...

    @abstractmethod
    async def modify_process(self, process: ProcessDefinition, command: str, correction: str = '') -> ModificationResult: ...

    @abstractmethod
    async def audit_process(self, process: ProcessDefinition) -> AuditResult: ...
