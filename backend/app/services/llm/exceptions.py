class ProviderError(Exception):
    """Only safe, application-owned messages cross the provider boundary."""
    def __init__(self, message, *, retryable=False):
        super().__init__(message)
        self.retryable = retryable
