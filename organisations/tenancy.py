"""Multi-tenancy core: the current-organisation ContextVar, the fail-closed
scoped manager, and the abstract base models every tenant table inherits.

See docs/02-architecture.md §3 and docs/04-design.md §1.1, and the tenancy
rules in CLAUDE.md.
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from django.db import models

if TYPE_CHECKING:
    from organisations.models import Organisation

current_org: contextvars.ContextVar[Any] = contextvars.ContextVar("current_org", default=None)


class TenantNotSet(Exception):
    """Raised when tenant-scoped code runs with no organisation in context."""


def get_current_org() -> Organisation | None:
    return current_org.get()


@contextmanager
def tenant_context(org_or_id: Organisation | Any) -> Iterator[Organisation]:
    """Sets `current_org` for the duration of the block. Accepts an
    Organisation instance or its id. Always resets afterwards, even on error.

    Used in Celery tasks (which take IDs, not instances) and management
    commands, per CLAUDE.md's Celery conventions.
    """
    from organisations.models import Organisation

    org = (
        org_or_id if isinstance(org_or_id, Organisation) else Organisation.objects.get(pk=org_or_id)
    )
    token = current_org.set(org)
    try:
        yield org
    finally:
        current_org.reset(token)


class TenantScopedManager(models.Manager):
    """Filters every queryset by the current organisation. Raises
    TenantNotSet rather than silently returning cross-tenant data."""

    def get_queryset(self) -> models.QuerySet:
        org = get_current_org()
        if org is None:
            raise TenantNotSet(
                f"No organisation set for {self.model.__name__}.objects. "
                "Use organisations.tenancy.tenant_context(), or "
                f"{self.model.__name__}.unscoped for migrations/admin/Celery fan-out."
            )
        return super().get_queryset().filter(organisation=org)


class TenantModel(models.Model):
    """Abstract base for every tenant-owned model."""

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    organisation = models.ForeignKey(
        "organisations.Organisation", on_delete=models.PROTECT, db_index=False
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TenantScopedManager()
    # CLAUDE.md requires this exact order (objects, then unscoped) so
    # `objects` stays the default manager; ruff's DJ012 doesn't recognise a
    # custom Manager subclass declared before a plain models.Manager().
    unscoped = models.Manager()  # noqa: DJ012

    class Meta:
        abstract = True
        # `_base_manager` (used for a *forward* FK descriptor, e.g.
        # `task.location`, and for cascade-delete collection) is `unscoped`,
        # so that access doesn't need a `tenant_context()`. Note this does
        # NOT extend to reverse relations: `organisation.locations.all()`
        # still uses `objects` (Django builds reverse managers from
        # `_default_manager`'s class, not `_base_manager`) and will raise
        # TenantNotSet outside a tenant_context(), same as `Model.objects`.
        base_manager_name = "unscoped"

    def save(self, *args: Any, **kwargs: Any) -> None:
        org = get_current_org()
        if self.organisation_id is None:
            if org is None:
                raise TenantNotSet(
                    f"Cannot save a new {type(self).__name__} without an "
                    "organisation. Set one explicitly or use tenant_context()."
                )
            self.organisation_id = org.id
        elif org is not None and str(self.organisation_id) != str(org.id):
            raise ValueError(
                f"Refusing to save {type(self).__name__} for organisation "
                f"{self.organisation_id} while the current context is {org.id}."
            )
        super().save(*args, **kwargs)


class SyncedTenantModel(TenantModel):
    """Abstract base for tenant rows that phones pull (docs/02 §4.4).

    `updated_seq` is stamped by a PostgreSQL trigger (see
    organisations.migrations_utils.install_seq_trigger), not by Django, so
    that it also advances on QuerySet.update().
    """

    updated_seq = models.BigIntegerField(null=True, editable=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta(TenantModel.Meta):
        abstract = True
