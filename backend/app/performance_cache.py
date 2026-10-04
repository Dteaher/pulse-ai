"""Bounded, session-scoped cache and singleflight of validated API results.

In-flight failures are never cached. No request text or credentials are stored
in keys. A waiter cannot cancel the owner's operation.
"""
import asyncio
from collections import OrderedDict
from copy import deepcopy
import hashlib
import json
import unicodedata
from pathlib import Path
from time import monotonic

from .telemetry import profile

_version_signature = None
_version_digest = None


def implementation_version():
    global _version_signature, _version_digest
    root = Path(__file__).parent
    files = sorted(path for path in root.rglob('*') if path.suffix in {'.py', '.txt', '.xsd'} and '__pycache__' not in path.parts)
    signature = tuple((str(path), path.stat().st_mtime_ns, path.stat().st_size) for path in files)
    if signature == _version_signature:
        return _version_digest
    # Changes to prompts, DTO, validator, builder or DI invalidate old entries.
    digest = hashlib.sha256(b'pulse-performance-v1')
    for path in files:
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    _version_signature, _version_digest = signature, digest.hexdigest()
    return _version_digest


class ResultCache:
    def __init__(self, capacity=64, ttl=300, max_bytes=16_000_000, max_inflight=128):
        self.capacity, self.ttl, self.max_bytes = capacity, ttl, max_bytes
        self.max_inflight = max_inflight
        self.entries = OrderedDict()
        self.inflight = {}
        self.bytes = 0

    def clear(self):
        self.entries.clear()
        self.bytes = 0

    async def get_or_compute(self, scope, operation, body, version, compute):
        if not scope or self.capacity == 0:
            return await compute()
        body = dict(body)
        for field in ('text', 'original_text'):
            if isinstance(body.get(field), str):
                body[field] = unicodedata.normalize('NFC', body[field].replace('\r\n', '\n').strip())
        raw = json.dumps([scope, operation, body, version], ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        key = hashlib.sha256(raw.encode()).hexdigest()
        now = monotonic()
        # Expiry removes retained payloads, not merely lookup eligibility.
        for expired, (until, _, size) in list(self.entries.items()):
            if until <= now:
                self.bytes -= size
                del self.entries[expired]
        cached = self.entries.get(key)
        if cached:
            self.entries.move_to_end(key)
            current = profile.get()
            if current:
                current.cache_hit = True
            return deepcopy(cached[1])
        if key in self.inflight:
            current = profile.get()
            if current:
                current.deduplicated = True
            return deepcopy(await asyncio.shield(self.inflight[key]))
        if len(self.inflight) >= self.max_inflight:
            # Bound retained Futures even if many different requests arrive together.
            return await compute()
        future = asyncio.get_running_loop().create_future()
        self.inflight[key] = future
        try:
            result = await compute()
            # Only post-validated graphs or validated graph-free question results.
            successful = bool(result.get('xml')) or (result.get('status') == 'clarification_required' and result.get('preflight')) or result.get('llm_audit') is True
            if successful:
                from fastapi.encoders import jsonable_encoder
                size = len(json.dumps(jsonable_encoder(result), ensure_ascii=False).encode())
                if size <= self.max_bytes:
                    while self.entries and (len(self.entries) >= self.capacity or self.bytes + size > self.max_bytes):
                        _, (_, _, removed) = self.entries.popitem(last=False)
                        self.bytes -= removed
                    self.entries[key] = (monotonic() + self.ttl, deepcopy(result), size)
                    self.bytes += size
            future.set_result(result)
            return result
        except BaseException as exc:
            future.set_exception(exc)
            future.exception()  # Consume it also when there are no waiters.
            raise
        finally:
            self.inflight.pop(key, None)
