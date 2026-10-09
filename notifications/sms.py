"""render_sms(kind, context): renders notifications/templates/sms/<kind>.txt
and collapses it to one line, so a template author's line breaks don't cost
GSM-7 segment budget. Templates use `truncatechars` on variable-length parts
and build links from settings.SITE_URL (never a hardcoded host).
"""

import re

from django.template.loader import render_to_string

_WHITESPACE_RE = re.compile(r"\s+")


def render_sms(kind: str, context: dict) -> str:
    rendered = render_to_string(f"sms/{kind}.txt", context)
    return _WHITESPACE_RE.sub(" ", rendered).strip()
