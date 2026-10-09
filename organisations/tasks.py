"""Celery tasks for the organisations app.

`heartbeat` is a harmless scaffold task wired into Beat (workflow/celery.py)
so the worker/beat services have something real to run and healthcheck
against before the actual background jobs (docs/02-architecture.md §5) are
built.
"""

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(time_limit=30, soft_time_limit=20)
def heartbeat() -> str:
    logger.info("organisations.tasks.heartbeat: ok")
    return "ok"
