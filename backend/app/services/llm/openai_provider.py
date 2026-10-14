from .openai_compatible_provider import OpenAICompatibleProvider


class OpenAIProvider(OpenAICompatibleProvider):
    name = 'openai'

    def __init__(self, key, base_url, model, structured=True, timeout=90, max_tokens=16000, max_retries=2, connect_timeout=10, trust_env=True):
        super().__init__(key, base_url, model, structured, timeout, max_tokens, max_retries, connect_timeout=connect_timeout, trust_env=trust_env)

    def token_budget(self):
        return {'max_completion_tokens': self.max_tokens}
