"""Celery app for the workflow project.

Beat's schedule is defined here in code, so it is version-controlled (see
docs/02-architecture.md §5). Retention purges are still to come.
"""

import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "workflow.settings.dev")

app = Celery("workflow")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

app.conf.beat_schedule = {
    # docs/02-architecture.md §5: runs up to 48 h ahead, idempotent.
    "checklists-generate-runs": {
        "task": "checklists.tasks.generate_checklist_runs",
        "schedule": crontab(minute="*/15"),
    },
    # docs/02-architecture.md §5: both idempotent, both every minute.
    "notifications-scan-due": {
        "task": "notifications.tasks.scan_due",
        "schedule": crontab(minute="*"),
    },
    "notifications-dispatch-due": {
        "task": "notifications.tasks.dispatch_due",
        "schedule": crontab(minute="*"),
    },
    "organisations-heartbeat": {
        "task": "organisations.tasks.heartbeat",
        "schedule": crontab(minute="*/5"),
    },
}
