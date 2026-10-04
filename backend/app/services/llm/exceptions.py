import re


class ProviderError(Exception):
    """Only safe, application-owned messages cross the provider boundary."""
    def __init__(self, message, *, retryable=False, reason='api_error', retry_after=None):
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after
        self.reason = reason if isinstance(reason, str) and (reason in {'timeout', 'network_error', 'provider_unavailable', 'model_unavailable', 'invalid_response', 'token_limit', 'output_truncated', 'api_error', 'semantic_validation', 'graph_validation'} or re.fullmatch(r'http_[1-5]\d{2}', reason)) else 'api_error'
