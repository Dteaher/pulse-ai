from .openai_compatible_provider import OpenAICompatibleProvider


class MultiAIProvider(OpenAICompatibleProvider):
    """MultiAI's OpenAI-compatible transport; shared validated result contract."""

    name = 'multiai'

    def __init__(self, *args, reasoning_effort=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.reasoning_effort = reasoning_effort

    def request_options(self):
        operation = 'corrective' if getattr(self, '_corrective', False) else getattr(self, '_operation', '')
        effort = getattr(self, 'reasoning_by_operation', {}).get(operation) or self.reasoning_effort
        return {'reasoning_effort': effort} if effort else {}
