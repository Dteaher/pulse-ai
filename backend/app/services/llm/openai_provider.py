from .openai_compatible_provider import OpenAICompatibleProvider


class OpenAIProvider(OpenAICompatibleProvider):
    name = 'openai'

    def __init__(self, key, base_url, model, structured=True, timeout=90, max_tokens=16000, max_retries=2):
        super().__init__(key, base_url, model, structured, timeout, max_tokens, max_retries)

    def token_budget(self):
        return {'max_completion_tokens': self.max_tokens}
