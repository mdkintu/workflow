"""factory_boy factories for every model (ADR-16 / CLAUDE.md "Tests").

Tenant models take `organisation` from a passed org: pass
`organisation=some_org` and every nested SubFactory reuses that same org via
`factory.SelfAttribute("..organisation")`, so generated rows stay internally
consistent (CLAUDE.md: "foreign keys between tenant records point to the
same organisation").

`TenantDjangoModelFactory` creates through `Model.unscoped` so factories work
whether or not a `tenant_context()` is active — tests are system code, like
migrations/admin/Celery fan-out (CLAUDE.md's `.unscoped` rule targets
request-handling code, not test infrastructure).
"""

from __future__ import annotations

import uuid
from datetime import time, timedelta

import factory
from django.utils import timezone
from factory.django import DjangoModelFactory

from accounts.models import PinSetupToken, User
from checklists.models import (
    ChecklistItem,
    ChecklistRun,
    ChecklistRunItem,
    ChecklistRunItemTick,
    ChecklistTemplate,
    RecurrenceKind,
    RecurrenceRule,
)
from notifications.models import (
    Notification,
    NotificationChannel,
    NotificationKind,
    NotificationStatus,
    SmsUsage,
)
from organisations.models import (
    AuditEvent,
    Location,
    MemberActivity,
    Membership,
    MembershipLocation,
    MembershipRole,
    Organisation,
    Shift,
    ShiftAssignment,
)
from sync.models import OfflineSyncLog, SyncResultStatus
from tasks.models import Task, TaskComment, TaskPhoto, TaskStatus

PARENT_ORG = factory.SelfAttribute("..organisation")


class TenantDjangoModelFactory(DjangoModelFactory):
    class Meta:
        abstract = True

    @classmethod
    def _get_manager(cls, model_class):
        return model_class.unscoped


class UserFactory(DjangoModelFactory):
    class Meta:
        model = User
        skip_postgeneration_save = True

    phone_e164 = factory.Sequence(lambda n: f"+2567{n:08d}")
    name = factory.Sequence(lambda n: f"Test User {n}")
    must_set_pin = False

    @factory.post_generation
    def pin(self, create, extracted, **kwargs):  # noqa: FBT001
        self.set_password(extracted or "1234")
        if create:
            self.save(update_fields=["password"])


class PinSetupTokenFactory(DjangoModelFactory):
    class Meta:
        model = PinSetupToken

    user = factory.SubFactory(UserFactory)
    code_hash = factory.Sequence(lambda n: f"hash{n}")
    expires_at = factory.LazyFunction(lambda: timezone.now() + timedelta(hours=24))


class OrganisationFactory(DjangoModelFactory):
    class Meta:
        model = Organisation

    name = factory.Sequence(lambda n: f"Test Org {n}")
    slug = factory.Sequence(lambda n: f"test-org-{n}")


class LocationFactory(TenantDjangoModelFactory):
    class Meta:
        model = Location

    organisation = factory.SubFactory(OrganisationFactory)
    name = factory.Sequence(lambda n: f"Location {n}")


class MembershipFactory(TenantDjangoModelFactory):
    class Meta:
        model = Membership

    organisation = factory.SubFactory(OrganisationFactory)
    user = factory.SubFactory(UserFactory)
    role = MembershipRole.STAFF


class MembershipLocationFactory(TenantDjangoModelFactory):
    class Meta:
        model = MembershipLocation

    organisation = factory.SubFactory(OrganisationFactory)
    membership = factory.SubFactory(MembershipFactory, organisation=PARENT_ORG)
    location = factory.SubFactory(LocationFactory, organisation=PARENT_ORG)


class MemberActivityFactory(TenantDjangoModelFactory):
    class Meta:
        model = MemberActivity

    organisation = factory.SubFactory(OrganisationFactory)
    membership = factory.SubFactory(MembershipFactory, organisation=PARENT_ORG)


class ShiftFactory(TenantDjangoModelFactory):
    class Meta:
        model = Shift

    organisation = factory.SubFactory(OrganisationFactory)
    location = factory.SubFactory(LocationFactory, organisation=PARENT_ORG)
    name = "Morning"
    start_time = time(6, 0)
    end_time = time(14, 0)
    weekdays = [0, 1, 2, 3, 4]


class ShiftAssignmentFactory(TenantDjangoModelFactory):
    class Meta:
        model = ShiftAssignment

    organisation = factory.SubFactory(OrganisationFactory)
    shift = factory.SubFactory(ShiftFactory, organisation=PARENT_ORG)
    membership = factory.SubFactory(MembershipFactory, organisation=PARENT_ORG)
    date = factory.LazyFunction(timezone.localdate)


class AuditEventFactory(TenantDjangoModelFactory):
    class Meta:
        model = AuditEvent

    organisation = factory.SubFactory(OrganisationFactory)
    action = "task.create"
    target_type = "task"
    target_id = factory.LazyFunction(uuid.uuid4)


class TaskFactory(TenantDjangoModelFactory):
    class Meta:
        model = Task

    organisation = factory.SubFactory(OrganisationFactory)
    title = factory.Sequence(lambda n: f"Task {n}")
    location = factory.SubFactory(LocationFactory, organisation=PARENT_ORG)
    assignee_location = True
    due_at = factory.LazyFunction(lambda: timezone.now() + timedelta(hours=1))
    created_by = factory.SubFactory(MembershipFactory, organisation=PARENT_ORG)
    status = TaskStatus.PENDING


class TaskPhotoFactory(TenantDjangoModelFactory):
    class Meta:
        model = TaskPhoto

    organisation = factory.SubFactory(OrganisationFactory)
    task = factory.SubFactory(TaskFactory, organisation=PARENT_ORG)
    file = factory.Sequence(lambda n: f"org/x/photos/{n}.jpg")
    thumb = factory.Sequence(lambda n: f"org/x/photos/{n}_t.jpg")
    bytes = 12345
    sha256 = factory.Sequence(lambda n: f"{n:064d}")
    width = 1280
    height = 960
    uploaded_by = factory.SubFactory(MembershipFactory, organisation=PARENT_ORG)


class TaskCommentFactory(TenantDjangoModelFactory):
    class Meta:
        model = TaskComment

    organisation = factory.SubFactory(OrganisationFactory)
    task = factory.SubFactory(TaskFactory, organisation=PARENT_ORG)
    author = factory.SubFactory(MembershipFactory, organisation=PARENT_ORG)
    body = "A comment"
    device_time = factory.LazyFunction(timezone.now)


class ChecklistTemplateFactory(TenantDjangoModelFactory):
    class Meta:
        model = ChecklistTemplate

    organisation = factory.SubFactory(OrganisationFactory)
    name = factory.Sequence(lambda n: f"Checklist {n}")
    location = factory.SubFactory(LocationFactory, organisation=PARENT_ORG)
    created_by = factory.SubFactory(MembershipFactory, organisation=PARENT_ORG)


class ChecklistItemFactory(TenantDjangoModelFactory):
    class Meta:
        model = ChecklistItem

    organisation = factory.SubFactory(OrganisationFactory)
    template = factory.SubFactory(ChecklistTemplateFactory, organisation=PARENT_ORG)
    order = factory.Sequence(lambda n: n + 1)
    label = factory.Sequence(lambda n: f"Item {n}")


class RecurrenceRuleFactory(TenantDjangoModelFactory):
    class Meta:
        model = RecurrenceRule

    organisation = factory.SubFactory(OrganisationFactory)
    template = factory.SubFactory(ChecklistTemplateFactory, organisation=PARENT_ORG)
    kind = RecurrenceKind.DAILY
    times = [time(7, 0)]
    starts_on = factory.LazyFunction(timezone.localdate)


class ChecklistRunFactory(TenantDjangoModelFactory):
    class Meta:
        model = ChecklistRun

    organisation = factory.SubFactory(OrganisationFactory)
    template = factory.SubFactory(ChecklistTemplateFactory, organisation=PARENT_ORG)
    name = factory.Sequence(lambda n: f"Run {n}")
    location = factory.SubFactory(LocationFactory, organisation=PARENT_ORG)
    occurrence_start = factory.LazyFunction(timezone.now)
    due_at = factory.LazyFunction(lambda: timezone.now() + timedelta(hours=1))


class ChecklistRunItemFactory(TenantDjangoModelFactory):
    class Meta:
        model = ChecklistRunItem

    organisation = factory.SubFactory(OrganisationFactory)
    run = factory.SubFactory(ChecklistRunFactory, organisation=PARENT_ORG)
    order = factory.Sequence(lambda n: n + 1)
    label = factory.Sequence(lambda n: f"Run item {n}")


class ChecklistRunItemTickFactory(TenantDjangoModelFactory):
    class Meta:
        model = ChecklistRunItemTick

    organisation = factory.SubFactory(OrganisationFactory)
    run_item = factory.SubFactory(ChecklistRunItemFactory, organisation=PARENT_ORG)
    membership = factory.SubFactory(MembershipFactory, organisation=PARENT_ORG)
    device_time = factory.LazyFunction(timezone.now)
    trusted_time = factory.LazyFunction(timezone.now)


class NotificationFactory(TenantDjangoModelFactory):
    class Meta:
        model = Notification

    organisation = factory.SubFactory(OrganisationFactory)
    recipient = factory.SubFactory(MembershipFactory, organisation=PARENT_ORG)
    to_e164 = factory.Sequence(lambda n: f"+2567{n:08d}")
    channel = NotificationChannel.SMS
    kind = NotificationKind.REMINDER
    body = "Reminder"
    dedupe_key = factory.Sequence(lambda n: f"dedupe-{n}")
    status = NotificationStatus.QUEUED
    send_after = factory.LazyFunction(timezone.now)


class SmsUsageFactory(TenantDjangoModelFactory):
    class Meta:
        model = SmsUsage

    organisation = factory.SubFactory(OrganisationFactory)
    month = factory.LazyFunction(lambda: timezone.localdate().replace(day=1))


class OfflineSyncLogFactory(TenantDjangoModelFactory):
    class Meta:
        model = OfflineSyncLog

    id = factory.LazyFunction(uuid.uuid4)
    organisation = factory.SubFactory(OrganisationFactory)
    membership = factory.SubFactory(MembershipFactory, organisation=PARENT_ORG)
    device_id = factory.LazyFunction(uuid.uuid4)
    kind = "task.complete"
    device_time = factory.LazyFunction(timezone.now)
    result_status = SyncResultStatus.APPLIED
    result_json = factory.LazyFunction(dict)
