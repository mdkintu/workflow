"""Celery tasks for checklists (docs/02-architecture.md §5).

Beat runs generate_checklist_runs every 15 minutes; it fans out one task per
active organisation (tasks take IDs, not objects — CLAUDE.md "Celery"), and
each is idempotent (checklists.generator), so overlapping or repeated runs
are harmless.
"""

from __future__ import annotations

import logging

from celery import shared_task

from checklists.generator import generate_runs
from organisations.models import Organisation

logger = logging.getLogger(__name__)


@shared_task(time_limit=60, soft_time_limit=50, acks_late=True)
def generate_checklist_runs() -> int:
    org_ids = list(Organisation.objects.filter(is_active=True).values_list("id", flat=True))
    for org_id in org_ids:
        generate_checklist_runs_for_org.delay(str(org_id))
    return len(org_ids)


@shared_task(time_limit=300, soft_time_limit=270, acks_late=True)
def generate_checklist_runs_for_org(org_id: str) -> int:
    organisation = Organisation.objects.get(pk=org_id, is_active=True)
    created = generate_runs(organisation)
    if created:
        logger.info("checklists: generated %d runs for organisation %s", created, org_id)
    return created
