"""Design-system template tags (ADR-19, ADR-20).

`{% icon "bell" %}` inlines a vendored Lucide SVG; `{% icon "bell" size=16 class="text-danger" %}`
changes its size or adds classes. Unknown names raise, so a typo fails in tests.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path

from django import template
from django.conf import settings
from django.utils.html import format_html
from django.utils.safestring import SafeString, mark_safe

register = template.Library()

ICON_DIR = Path(settings.BASE_DIR) / "frontend" / "vendor" / "lucide"
_SVG_OPEN = re.compile(r"<svg\b[^>]*>", re.DOTALL)


@cache
def _icon_body(name: str) -> str:
    """The SVG's children (paths etc.), without the licence comment or <svg> tag."""
    if not re.fullmatch(r"[a-z0-9-]+", name):
        raise template.TemplateSyntaxError(f"Bad icon name {name!r}")
    path = ICON_DIR / f"{name}.svg"
    if not path.is_file():
        raise template.TemplateSyntaxError(f"Unknown icon {name!r} (see frontend/vendor/lucide/)")
    text = path.read_text()
    body = _SVG_OPEN.split(text, maxsplit=1)[1].rsplit("</svg>", 1)[0]
    return " ".join(line.strip() for line in body.splitlines() if line.strip())


@register.simple_tag
def icon(name: str, size: int = 20, **attrs: str) -> SafeString:
    css = ("icon " + attrs.pop("class", "")).strip()
    # The body comes from our own vendored, checksummed SVG files (ADR-20),
    # never from user input; the class and size are escaped/coerced.
    body = mark_safe(_icon_body(name))  # noqa: S308
    return format_html(
        '<svg xmlns="http://www.w3.org/2000/svg" width="{0}" height="{0}" viewBox="0 0 24 24"'
        ' fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"'
        ' stroke-linejoin="round" aria-hidden="true" focusable="false" class="{1}">{2}</svg>',
        int(size),
        css,
        body,
    )


@register.filter
def initials(name: str) -> str:
    """ "Grace Nakato" -> "GN"; for avatars."""
    parts = [p for p in str(name).split() if p]
    return "".join(p[0] for p in parts[:2]).upper() or "?"
