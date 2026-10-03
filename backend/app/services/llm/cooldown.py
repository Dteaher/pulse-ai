"""Process-local quota cooldowns; credentials are hashed and never logged."""
import hashlib
import math
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


def retry_delay(headers):
    value = headers.get('retry-after', '')
    try:
        seconds = float(value)
    except (ValueError, TypeError):
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return 60
    return max(1, min(seconds, 86400)) if math.isfinite(seconds) else 60


class ProviderCooldowns:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.deadlines = {}

    def key(self, provider):
        values = [provider.name, provider.model, getattr(provider, 'base_url', ''), getattr(provider, 'key', '')]
        return hashlib.sha256('\0'.join(values).encode()).hexdigest()

    def remaining(self, provider):
        return max(0, self.deadlines.get(self.key(provider), 0) - self.clock())

    def block(self, provider, seconds):
        now = self.clock()
        self.deadlines = {k: v for k, v in self.deadlines.items() if v > now}
        key = self.key(provider)
        self.deadlines[key] = max(self.deadlines.get(key, 0), now + (seconds or 60))


cooldowns = ProviderCooldowns()
