"""Every notifications/templates/sms/*.txt template must fit one 160-char
GSM-7 segment, even with worst-case (very long) variable content (ADR-10,
CLAUDE.md "SMS templates must fit one 160-character GSM-7 segment").
"""

import pytest

from notifications.sms import render_sms

SITE_URL = "https://workflow-demo-hotel.example.ug"

WORST_CASE_CONTEXT = {
    "reminder": {
        "task_title": "X" * 200,
        "due_time": "23:59",
        "location_name": "Y" * 200,
        "site_url": SITE_URL,
    },
    "overdue": {
        "task_title": "X" * 200,
        "due_time": "23:59",
        "location_name": "Y" * 200,
        "site_url": SITE_URL,
    },
    "escalation": {
        "task_title": "X" * 200,
        "location_name": "Y" * 200,
        "due_time": "23:59",
        "site_url": SITE_URL,
    },
    "flag": {
        "staff_name": "Z" * 200,
        "task_title": "X" * 200,
        "reason": "W" * 200,
        "site_url": SITE_URL,
    },
    "rejected": {
        "task_title": "X" * 200,
        "reason": "W" * 200,
        "site_url": SITE_URL,
    },
    "synced_late": {
        "task_title": "X" * 200,
        "completed_time": "23:59",
        "site_url": SITE_URL,
    },
    "daily_summary": {
        "date": "2026-10-14",
        "done_count": 9999,
        "overdue_count": 9999,
        "flagged_count": 9999,
        "site_url": SITE_URL,
    },
    "pin_setup": {
        "code": "123456",
        "site_url": SITE_URL,
    },
    "overdue_combined": {
        "count": 999,
        "location_name": "Y" * 200,
        "due_time": "23:59",
        "site_url": SITE_URL,
    },
    "budget_warning": {
        "used": 99999,
        "cap": 99999,
        "site_url": SITE_URL,
    },
}


@pytest.mark.parametrize("kind", sorted(WORST_CASE_CONTEXT))
def test_sms_template_fits_one_gsm7_segment(kind):
    body = render_sms(kind, WORST_CASE_CONTEXT[kind])
    assert len(body) <= 160, f"{kind} is {len(body)} chars: {body!r}"


def test_render_sms_collapses_whitespace():
    body = render_sms("pin_setup", {"code": "999999", "site_url": SITE_URL})
    assert "\n" not in body
    assert "  " not in body
