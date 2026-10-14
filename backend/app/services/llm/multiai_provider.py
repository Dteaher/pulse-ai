from .openai_compatible_provider import OpenAICompatibleProvider
from .performance import operation_reasoning


class MultiAIProvider(OpenAICompatibleProvider):
    """MultiAI's OpenAI-compatible transport; shared validated result contract."""

    name = 'multiai'

    def __init__(self, *args, reasoning_effort=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.reasoning_effort = reasoning_effort

    def request_options(self):
        effort = operation_reasoning(self)
        return {'reasoning_effort': effort} if effort else {}
