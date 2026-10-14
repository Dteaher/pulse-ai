from .openai_compatible_provider import OpenAICompatibleProvider
from .performance import operation_reasoning


class PolzaProvider(OpenAICompatibleProvider):
    """Polza transport shares validated DTOs, retries and format negotiation."""

    name = 'polza'

    def __init__(self, *args, reasoning_effort=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.reasoning_effort = reasoning_effort

    def request_options(self):
        effort = operation_reasoning(self)
        return {'reasoning': {'effort': effort}} if effort else {}
