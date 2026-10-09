"""Small JSON logging helpers.

Kept deliberately simple (per CLAUDE.md): one formatter that emits one JSON
object per line, and one filter that redacts Ugandan phone numbers
(+256XXXXXXXXX) so they never reach stdout/log storage. See CLAUDE.md
"Privacy" and docs/02-architecture.md §11.
"""

import json
import logging
import re

_PHONE_RE = re.compile(r"\+256\d{9}")


class RedactPhoneNumbersFilter(logging.Filter):
    """Redacts +256XXXXXXXXX phone numbers from log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - never break logging
            return True
        if _PHONE_RE.search(message):
            record.msg = _PHONE_RE.sub("[REDACTED]", message)
            record.args = ()
        return True


class JsonFormatter(logging.Formatter):
    """A minimal one-line-per-record JSON formatter."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
        }
        request_id = getattr(record, "request_id", None)
        if request_id:
            payload["request_id"] = request_id
        org_id = getattr(record, "org_id", None)
        if org_id:
            payload["org_id"] = org_id
        membership_id = getattr(record, "membership_id", None)
        if membership_id:
            payload["membership_id"] = membership_id
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)
