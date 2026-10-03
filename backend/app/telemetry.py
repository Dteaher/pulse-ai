"""Request context for safe logs across async provider and pipeline calls."""
import logging
from contextvars import ContextVar

request_id = ContextVar('pulse_request_id', default='outside_http')
operation = ContextVar('pulse_operation', default='internal')


class RequestLogContext(logging.Filter):
    def filter(self, record):
        record.request_id = request_id.get()
        record.operation = operation.get()
        return True
