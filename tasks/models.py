"""Task, TaskPhoto, TaskComment. Field lists, constraints and indexes follow
docs/04-design.md §1.2 exactly. Status changes go only through
tasks/transitions.py (CLAUDE.md). `Task.objects` adds the visibility scopes
from the docs/04-design.md §2 matrix and the derived overdue flag (ADR-17).
"""

from __future__ import annotations

import zoneinfo

from django.db import models
from django.db.models import Case, Exists, OuterRef, Q, Value, When
from django.db.models.functions import Now, TruncDate
from django.utils import timezone

from organisations.tenancy import SyncedTenantModel, TenantModel, TenantScopedManager

_has_membership = models.Q(assignee_membership__isnull=False)
_has_shift = models.Q(assignee_shift__isnull=False)
_has_location_assignee = models.Q(assignee_location=True)

EXACTLY_ONE_ASSIGNEE = (
    (_has_membership & ~_has_shift & ~_has_location_assignee)
    | (~_has_membership & _has_shift & ~_has_location_assignee)
    | (~_has_membership & ~_has_shift & _has_location_assignee)
)


class TaskStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    IN_PROGRESS = "in_progress", "In progress"
    DONE = "done", "Done"
    FLAGGED = "flagged", "Flagged"
    CANCELLED = "cancelled", "Cancelled"


class FlagKind(models.TextChoices):
    PROBLEM = "problem", "Problem"
    REJECTED = "rejected", "Rejected"
    COMPLETED_AFTER_CANCEL = "completed_after_cancel", "Completed after cancel"


OPEN_STATUSES = (TaskStatus.PENDING, TaskStatus.IN_PROGRESS)


class TaskQuerySet(models.QuerySet):
    def annotate_overdue(self) -> TaskQuerySet:
        """Overdue is derived, never stored (ADR-17): past due and still
        pending/in progress. A flagged task counts as flagged, not overdue."""
        return self.annotate(
            is_overdue=Case(
                When(status__in=OPEN_STATUSES, due_at__lt=Now(), then=Value(True)),
                default=Value(False),
                output_field=models.BooleanField(),
            )
        )

    def mine(self, membership) -> TaskQuerySet:
        """The "mine" scope: assigned to this person, or to a shift they're
        rostered on for that shift date, or to a location where they're on
        any shift on the task's local due date (organisation timezone)."""
        from organisations.models import ShiftAssignment

        tz = zoneinfo.ZoneInfo(membership.organisation.timezone)
        rostered_on_shift = ShiftAssignment.objects.filter(
            membership=membership, shift=OuterRef("assignee_shift"), date=OuterRef("shift_date")
        )
        rostered_at_location = ShiftAssignment.objects.filter(
            membership=membership,
            shift__location=OuterRef("location"),
            date=OuterRef("local_due_date"),
        )
        return self.annotate(local_due_date=TruncDate("due_at", tzinfo=tz)).filter(
            Q(assignee_membership=membership)
            | Q(Exists(rostered_on_shift))
            | (Q(assignee_location=True) & Q(Exists(rostered_at_location)))
        )

    def visible_to(self, membership) -> TaskQuerySet:
        """docs/04-design.md §2: Staff see "mine"; Supervisors and Managers
        see their linked locations plus "mine" (no linked locations = all);
        Owners see everything. Always within one organisation — the tenant
        scoping comes from the manager, not from here."""
        from organisations.permissions import location_scope

        scope = location_scope(membership)
        if scope is None:
            return self
        if not scope:
            return self.mine(membership)
        mine_ids = self.model.objects.mine(membership).values("id")
        return self.filter(Q(location_id__in=scope) | Q(id__in=mine_ids))


class Task(SyncedTenantModel):
    title = models.CharField(max_length=120)
    description = models.TextField(max_length=1000, blank=True)
    location = models.ForeignKey(
        "organisations.Location", on_delete=models.PROTECT, related_name="tasks"
    )
    assignee_membership = models.ForeignKey(
        "organisations.Membership",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="assigned_tasks",
    )
    assignee_shift = models.ForeignKey(
        "organisations.Shift",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="assigned_tasks",
    )
    assignee_location = models.BooleanField(default=False)
    shift_date = models.DateField(null=True, blank=True)
    due_at = models.DateTimeField()
    status = models.CharField(max_length=20, choices=TaskStatus.choices, default=TaskStatus.PENDING)
    photo_required = models.BooleanField(default=True)
    reminder_lead_min = models.PositiveSmallIntegerField(null=True, blank=True)
    created_by = models.ForeignKey(
        "organisations.Membership", on_delete=models.PROTECT, related_name="created_tasks"
    )
    started_by = models.ForeignKey(
        "organisations.Membership",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="started_tasks",
    )
    completed_by = models.ForeignKey(
        "organisations.Membership",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="completed_tasks",
    )
    completed_at_device = models.DateTimeField(null=True, blank=True)
    completed_at_trusted = models.DateTimeField(null=True, blank=True)
    completed_received_at = models.DateTimeField(null=True, blank=True)
    due_at_when_completed = models.DateTimeField(null=True, blank=True)
    time_untrusted = models.BooleanField(default=False)
    # null=True is intentional: None means "not flagged", distinct from any
    # choice value (docs/04-design.md §1.2).
    flag_kind = models.CharField(  # noqa: DJ001
        max_length=30, choices=FlagKind.choices, null=True, blank=True
    )
    flag_reason = models.CharField(max_length=300, blank=True)
    flagged_by = models.ForeignKey(
        "organisations.Membership",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="flagged_tasks",
    )
    flag_resolved_note = models.CharField(max_length=300, blank=True)
    # When it was cancelled: T9 compares an offline completion's trusted time
    # with this (docs/04-design.md §5.2).
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        "organisations.Membership",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cancelled_tasks",
    )
    recurrence_rule = models.ForeignKey(
        "TaskRecurrenceRule",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="task_occurrences",
    )
    occurrence_start = models.DateTimeField(
        null=True, blank=True, help_text="When this occurrence begins (for recurring tasks)"
    )

    # Tenant-scoped like every TenantModel, plus visible_to()/mine()/
    # annotate_overdue(). `unscoped` is still inherited from TenantModel.
    objects = TenantScopedManager.from_queryset(TaskQuerySet)()

    class Meta(SyncedTenantModel.Meta):
        constraints = [
            models.CheckConstraint(
                condition=EXACTLY_ONE_ASSIGNEE, name="chk_task_exactly_one_assignee"
            ),
            models.CheckConstraint(
                condition=models.Q(assignee_shift__isnull=True)
                | models.Q(shift_date__isnull=False),
                name="chk_task_shift_date_required",
            ),
            models.CheckConstraint(
                condition=~models.Q(status=TaskStatus.FLAGGED) | models.Q(flag_kind__isnull=False),
                name="chk_task_flagged_has_kind",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organisation", "status", "due_at"], name="idx_task_org_status_due"
            ),
            models.Index(
                fields=["organisation", "location", "due_at"], name="idx_task_org_loc_due"
            ),
            models.Index(
                fields=["organisation", "assignee_membership", "due_at"],
                name="idx_task_org_assignee_due",
            ),
            models.Index(
                fields=["organisation", "assignee_shift", "shift_date"],
                name="idx_task_org_shift_date",
            ),
            models.Index(fields=["organisation", "updated_seq"], name="idx_task_org_seq"),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def is_overdue(self) -> bool:
        annotated = self.__dict__.get("_is_overdue_annotation")
        if annotated is not None:
            return annotated
        return self.status in OPEN_STATUSES and self.due_at < timezone.now()

    @is_overdue.setter
    def is_overdue(self, value: bool) -> None:
        # Lets annotate_overdue() set the same name on each instance.
        self.__dict__["_is_overdue_annotation"] = value

    @property
    def assignee_label(self) -> str:
        if self.assignee_membership_id:
            return self.assignee_membership.name
        if self.assignee_shift_id:
            return f"{self.assignee_shift.name} ({self.shift_date:%a %d %b})"
        return str(self.location)


class TaskPhoto(SyncedTenantModel):
    """The single photo table: task proof, checklist tick photos and comment
    photos. The client generates `id` and uploads before the mutation that
    references it (docs/04-design.md §1.2)."""

    task = models.ForeignKey(
        Task, on_delete=models.CASCADE, null=True, blank=True, related_name="photos"
    )
    checklist_run = models.ForeignKey(
        "checklists.ChecklistRun",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="photos",
    )
    file = models.CharField(max_length=200)
    thumb = models.CharField(max_length=200)
    bytes = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    width = models.PositiveSmallIntegerField()
    height = models.PositiveSmallIntegerField()
    taken_at_device = models.DateTimeField(null=True, blank=True)
    uploaded_by = models.ForeignKey(
        "organisations.Membership", on_delete=models.PROTECT, related_name="uploaded_photos"
    )
    linked_at = models.DateTimeField(null=True, blank=True)

    class Meta(SyncedTenantModel.Meta):
        indexes = [
            models.Index(fields=["organisation", "task"], name="idx_taskphoto_org_task"),
            models.Index(fields=["organisation", "linked_at"], name="idx_taskphoto_org_linked"),
            models.Index(fields=["organisation", "updated_seq"], name="idx_taskphoto_org_seq"),
        ]

    def __str__(self) -> str:
        return self.file


class TaskComment(SyncedTenantModel):
    """A comment on a task or a checklist run. A flag raised by staff is a
    comment with `is_flag=True`."""

    task = models.ForeignKey(
        Task, on_delete=models.CASCADE, null=True, blank=True, related_name="comments"
    )
    checklist_run = models.ForeignKey(
        "checklists.ChecklistRun",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="comments",
    )
    author = models.ForeignKey(
        "organisations.Membership", on_delete=models.PROTECT, related_name="authored_comments"
    )
    body = models.CharField(max_length=500)
    photo = models.ForeignKey(
        TaskPhoto, on_delete=models.SET_NULL, null=True, blank=True, related_name="comments"
    )
    is_flag = models.BooleanField(default=False)
    device_time = models.DateTimeField()
    hidden_by = models.ForeignKey(
        "organisations.Membership",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="hidden_comments",
    )

    class Meta(SyncedTenantModel.Meta):
        constraints = [
            models.CheckConstraint(
                condition=(
                    (models.Q(task__isnull=False) & models.Q(checklist_run__isnull=True))
                    | (models.Q(task__isnull=True) & models.Q(checklist_run__isnull=False))
                ),
                name="chk_taskcomment_exactly_one_parent",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organisation", "task", "device_time"],
                name="idx_taskcomment_org_task_time",
            ),
            models.Index(
                fields=["organisation", "checklist_run", "device_time"],
                name="idx_taskcomment_org_run_time",
            ),
            models.Index(fields=["organisation", "updated_seq"], name="idx_taskcomment_org_seq"),
        ]
        ordering = ["device_time", "created_at"]

    def __str__(self) -> str:
        return self.body[:40]


class TaskRecurrenceRuleQuerySet(models.QuerySet):
    def active(self) -> "TaskRecurrenceRuleQuerySet":
        return self.filter(is_active=True)


class TaskRecurrenceRule(TenantModel):
    """Template for recurring self-tasks. One rule generates multiple Task
    occurrences on a schedule (daily, weekly, at shift start)."""

    DAILY = "daily"
    WEEKLY = "weekly"
    SHIFT_START = "shift_start"

    KIND_CHOICES = [
        (DAILY, "Daily"),
        (WEEKLY, "Weekly"),
        (SHIFT_START, "At shift start"),
    ]

    created_by = models.ForeignKey(
        "organisations.Membership", on_delete=models.PROTECT, related_name="created_recurrence_rules"
    )
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    times = models.JSONField(
        default=list, help_text="List of [hours, minutes] or empty for all-day"
    )
    weekdays = models.JSONField(default=list, help_text="List of 0-6 (Mon-Sun), empty for all days")
    available_before_min = models.PositiveSmallIntegerField(null=True, blank=True)
    due_offset_min = models.PositiveSmallIntegerField(default=0)
    starts_on = models.DateField()
    ends_on = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    objects = TenantScopedManager.from_queryset(TaskRecurrenceRuleQuerySet)()

    class Meta(TenantModel.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["organisation", "created_by", "starts_on"],
                name="unique_rule_per_creator_date",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organisation", "is_active", "starts_on"],
                name="idx_rule_org_active_start",
            ),
        ]
        verbose_name = "Task Recurrence Rule"

    def __str__(self) -> str:
        return f"{self.get_kind_display()} rule for {self.created_by.name}"
