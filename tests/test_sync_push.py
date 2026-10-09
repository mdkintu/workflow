"""POST /api/sync/push (docs/04-design.md §4.5, ADR-04): offline mutations,
idempotency, trusted time and the conflict rules."""

import json
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from checklists.models import ChecklistRunItemTick
from organisations.models import AuditEvent
from sync.models import OfflineSyncLog
from tasks.models import FlagKind, Task, TaskComment, TaskStatus
from tasks.transitions import Action, apply
from tests.factories import (
    ChecklistRunFactory,
    ChecklistRunItemFactory,
    LocationFactory,
    MembershipFactory,
    ShiftAssignmentFactory,
    ShiftFactory,
    TaskCommentFactory,
    TaskFactory,
    TaskPhotoFactory,
)

pytestmark = pytest.mark.django_db

DEVICE = str(uuid.uuid4())


def iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


@pytest.fixture
def w(org_a):
    now = timezone.now()
    main = LocationFactory(organisation=org_a)
    shift = ShiftFactory(organisation=org_a, location=main)
    peter = MembershipFactory(organisation=org_a, role="staff", user__name="Peter")
    grace = MembershipFactory(organisation=org_a, role="staff", user__name="Grace")
    supervisor = MembershipFactory(organisation=org_a, role="supervisor")
    for m in (peter, grace):
        ShiftAssignmentFactory(
            organisation=org_a, shift=shift, membership=m, date=timezone.localdate()
        )
    task = TaskFactory(
        organisation=org_a,
        location=main,
        assignee_location=False,
        assignee_membership=peter,
        photo_required=False,
        due_at=now + timedelta(hours=1),
    )
    Task.unscoped.filter(pk=task.pk).update(created_at=now - timedelta(hours=2))
    task.refresh_from_db()
    run = ChecklistRunFactory(
        organisation=org_a,
        location=main,
        shift=shift,
        shift_date=timezone.localdate(),
        occurrence_start=now - timedelta(minutes=5),
        due_at=now + timedelta(hours=1),
    )
    item = ChecklistRunItemFactory(organisation=org_a, run=run, order=1)
    must_do = ChecklistRunItemFactory(organisation=org_a, run=run, order=2, skippable=False)
    return SimpleNamespace(
        org=org_a,
        now=now,
        main=main,
        shift=shift,
        peter=peter,
        grace=grace,
        supervisor=supervisor,
        task=task,
        run=run,
        item=item,
        must_do=must_do,
    )


def mutation(kind, payload, device_time=None, mutation_id=None):
    return {
        "mutation_id": mutation_id or str(uuid.uuid4()),
        "kind": kind,
        "device_time": iso(device_time or timezone.now()),
        "payload": payload,
    }


def push(client, *mutations):
    response = client.post(
        "/api/sync/push",
        data=json.dumps({"device_id": DEVICE, "mutations": list(mutations)}),
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    return response.json()["results"]


def one(client, *args, **kwargs):
    [result] = push(client, mutation(*args, **kwargs))
    return result


# --- idempotency ---


def test_start_is_applied_and_logged(w, login_as):
    m = mutation("task.start", {"task_id": str(w.task.id)})
    [result] = push(login_as(w.peter), m)
    assert result["status"] == "applied"
    assert result["record"]["status"] == "in_progress"
    log = OfflineSyncLog.unscoped.get(pk=m["mutation_id"])
    assert log.membership == w.peter and log.kind == "task.start"
    assert str(log.device_id) == DEVICE


def test_a_replayed_mutation_returns_the_first_result(w, login_as):
    client = login_as(w.peter)
    m = mutation("task.start", {"task_id": str(w.task.id)})
    first = push(client, m)[0]
    again = push(client, m)[0]
    assert again["status"] == "duplicate"
    assert again["record"] == first["record"]
    assert AuditEvent.unscoped.filter(action="task.start").count() == 1


def test_rejections_are_replayed_too(w, login_as):
    client = login_as(w.peter)
    m = mutation(
        "task.flag", {"task_id": str(w.task.id), "reason": "", "comment_id": str(uuid.uuid4())}
    )
    assert push(client, m)[0]["code"] == "validation_error"
    assert push(client, m)[0]["status"] == "duplicate"


def test_someone_else_cannot_reuse_a_mutation_id(w, login_as, client):
    m = mutation("task.start", {"task_id": str(w.task.id)})
    push(login_as(w.peter), m)
    client.logout()
    assert push(login_as(w.grace), m)[0]["code"] == "forbidden"


def test_one_rejection_does_not_stop_the_rest(w, login_as):
    results = push(
        login_as(w.peter),
        mutation("task.start", {"task_id": str(uuid.uuid4())}),
        mutation("task.start", {"task_id": str(w.task.id)}),
    )
    assert [r["status"] for r in results] == ["rejected", "applied"]
    assert results[0]["code"] == "not_found"


# --- trusted time ---


def test_the_device_time_is_trusted_when_it_is_plausible(w, login_as):
    done_at = timezone.now() - timedelta(minutes=30)
    one(login_as(w.peter), "task.complete", {"task_id": str(w.task.id)}, device_time=done_at)
    w.task.refresh_from_db()
    assert abs(w.task.completed_at_trusted - done_at) < timedelta(seconds=1)
    assert w.task.time_untrusted is False


def test_a_device_clock_in_the_future_is_not_trusted(w, login_as):
    result = one(
        login_as(w.peter),
        "task.complete",
        {"task_id": str(w.task.id)},
        device_time=timezone.now() + timedelta(days=1),
    )
    w.task.refresh_from_db()
    assert w.task.time_untrusted is True
    assert w.task.completed_at_trusted <= timezone.now()
    assert result["code"] == "time_untrusted"


# --- conflict rules (ADR-04, docs/04-design.md §5.2 T9) ---


def test_completed_offline_before_the_cancel_counts_as_done(w, login_as):
    done_at = timezone.now() - timedelta(minutes=20)
    apply(w.task, Action.CANCEL, w.supervisor)
    result = one(
        login_as(w.peter), "task.complete", {"task_id": str(w.task.id)}, device_time=done_at
    )
    w.task.refresh_from_db()
    assert result["status"] == "applied"
    assert w.task.status == TaskStatus.DONE


def test_completed_offline_after_the_cancel_is_flagged_for_review(w, login_as):
    apply(w.task, Action.CANCEL, w.supervisor)
    Task.unscoped.filter(pk=w.task.pk).update(cancelled_at=timezone.now() - timedelta(minutes=30))
    result = one(
        login_as(w.peter),
        "task.complete",
        {"task_id": str(w.task.id)},
        device_time=timezone.now() - timedelta(minutes=5),
    )
    w.task.refresh_from_db()
    assert result["status"] == "applied"
    assert result["code"] == "task_cancelled_flagged"
    assert w.task.status == TaskStatus.FLAGGED
    assert w.task.flag_kind == FlagKind.COMPLETED_AFTER_CANCEL
    assert w.task.completed_by == w.peter


def test_a_second_completion_is_kept_as_evidence(w, login_as, client):
    shared = TaskFactory(
        organisation=w.org,
        location=w.main,
        assignee_location=False,
        assignee_shift=w.shift,
        shift_date=timezone.localdate(),
        photo_required=False,
    )
    one(login_as(w.peter), "task.complete", {"task_id": str(shared.id)})
    client.logout()
    result = one(login_as(w.grace), "task.complete", {"task_id": str(shared.id)})
    shared.refresh_from_db()
    assert result["status"] == "applied" and result["code"] == "already_done"
    assert shared.completed_by == w.peter
    assert TaskComment.unscoped.filter(task=shared, author=w.grace).exists()


def test_starting_a_task_that_is_already_done_is_an_invalid_transition(w, login_as):
    apply(w.task, Action.COMPLETE, w.peter)
    assert (
        one(login_as(w.peter), "task.start", {"task_id": str(w.task.id)})["code"]
        == "invalid_transition"
    )


# --- photos ---


def test_a_required_photo_must_be_referenced(w, login_as):
    Task.unscoped.filter(pk=w.task.pk).update(photo_required=True)
    assert (
        one(login_as(w.peter), "task.complete", {"task_id": str(w.task.id)})["code"]
        == "photo_required"
    )


def test_a_photo_not_uploaded_yet_is_retried_later_and_not_logged(w, login_as):
    Task.unscoped.filter(pk=w.task.pk).update(photo_required=True)
    m = mutation("task.complete", {"task_id": str(w.task.id), "photo_id": str(uuid.uuid4())})
    [result] = push(login_as(w.peter), m)
    assert result["status"] == "retry"
    assert result["code"] == "photo_not_uploaded"
    assert not OfflineSyncLog.unscoped.filter(pk=m["mutation_id"]).exists()


def test_complete_with_an_uploaded_photo(w, login_as):
    Task.unscoped.filter(pk=w.task.pk).update(photo_required=True)
    photo = TaskPhotoFactory(
        organisation=w.org, task=w.task, uploaded_by=w.peter, linked_at=timezone.now()
    )
    result = one(
        login_as(w.peter), "task.complete", {"task_id": str(w.task.id), "photo_id": str(photo.id)}
    )
    assert result["status"] == "applied"


# --- flags and comments ---


def test_flag_uses_the_phones_comment_id(w, login_as):
    comment_id = str(uuid.uuid4())
    one(
        login_as(w.peter),
        "task.flag",
        {"task_id": str(w.task.id), "reason": "No supplies", "note": "", "comment_id": comment_id},
    )
    comment = TaskComment.unscoped.get(pk=comment_id)
    assert comment.is_flag and comment.task_id == w.task.id


def test_add_a_comment_with_the_phones_id_and_time(w, login_as):
    comment_id, written = str(uuid.uuid4()), timezone.now() - timedelta(minutes=3)
    result = one(
        login_as(w.peter),
        "comment.add",
        {"comment_id": comment_id, "task_id": str(w.task.id), "body": "On it"},
        device_time=written,
    )
    comment = TaskComment.unscoped.get(pk=comment_id)
    assert result["status"] == "applied"
    assert comment.body == "On it" and comment.author == w.peter
    assert abs(comment.device_time - written) < timedelta(seconds=1)


def test_a_comment_on_a_task_you_cannot_see_is_not_found(w, login_as):
    other = TaskFactory(
        organisation=w.org,
        location=w.main,
        assignee_location=False,
        assignee_membership=w.supervisor,
    )
    result = one(
        login_as(w.peter),
        "comment.add",
        {"comment_id": str(uuid.uuid4()), "task_id": str(other.id), "body": "Hi"},
    )
    assert result["code"] == "not_found"


def test_an_empty_comment_is_a_validation_error(w, login_as):
    result = one(
        login_as(w.peter),
        "comment.add",
        {"comment_id": str(uuid.uuid4()), "task_id": str(w.task.id), "body": "  "},
    )
    assert result["code"] == "validation_error"


def test_a_flag_comment_on_a_run_flags_the_run(w, login_as):
    one(
        login_as(w.peter),
        "comment.add",
        {
            "comment_id": str(uuid.uuid4()),
            "run_id": str(w.run.id),
            "body": "Ice machine broken",
            "is_flag": True,
        },
    )
    w.run.refresh_from_db()
    assert w.run.status == "flagged"


def test_delete_own_comment(w, login_as):
    comment = TaskCommentFactory(organisation=w.org, task=w.task, author=w.peter)
    assert (
        one(login_as(w.peter), "comment.delete", {"comment_id": str(comment.id)})["status"]
        == "applied"
    )
    comment.refresh_from_db()
    assert comment.deleted_at is not None


def test_cannot_delete_someone_elses_comment(w, login_as):
    comment = TaskCommentFactory(organisation=w.org, task=w.task, author=w.grace)
    assert (
        one(login_as(w.peter), "comment.delete", {"comment_id": str(comment.id)})["code"]
        == "forbidden"
    )


# --- checklist ticks ---


def test_tick_uses_the_phones_id(w, login_as):
    tick_id = str(uuid.uuid4())
    result = one(
        login_as(w.peter), "run.item_tick", {"tick_id": tick_id, "run_item_id": str(w.item.id)}
    )
    assert result["status"] == "applied"
    assert ChecklistRunItemTick.unscoped.get(pk=tick_id).membership == w.peter


def test_two_offline_ticks_on_one_item_are_both_kept(w, login_as, client):
    one(
        login_as(w.peter),
        "run.item_tick",
        {"tick_id": str(uuid.uuid4()), "run_item_id": str(w.item.id)},
    )
    client.logout()
    result = one(
        login_as(w.grace),
        "run.item_tick",
        {"tick_id": str(uuid.uuid4()), "run_item_id": str(w.item.id)},
    )
    assert result["code"] == "already_done"
    assert ChecklistRunItemTick.unscoped.filter(run_item=w.item).count() == 2


def test_a_must_do_item_cannot_be_skipped(w, login_as):
    result = one(
        login_as(w.peter),
        "run.item_skip",
        {"tick_id": str(uuid.uuid4()), "run_item_id": str(w.must_do.id), "reason": "No time"},
    )
    assert result["code"] == "not_skippable"


# --- envelope ---


def test_an_unknown_kind(w, login_as):
    assert one(login_as(w.peter), "task.teleport", {})["code"] == "unknown_kind"


def test_a_malformed_envelope_is_a_400(w, login_as):
    response = login_as(w.peter).post("/api/sync/push", data="{}", content_type="application/json")
    assert response.status_code == 400
    assert response.json()["code"] == "bad_request"


def test_more_than_100_mutations_is_too_large(w, login_as):
    mutations = [mutation("task.start", {"task_id": str(w.task.id)}) for _ in range(101)]
    response = login_as(w.peter).post(
        "/api/sync/push",
        data=json.dumps({"device_id": DEVICE, "mutations": mutations}),
        content_type="application/json",
    )
    assert response.status_code == 413
    assert response.json()["code"] == "too_large"


def test_csrf_is_enforced(w, login_as):
    from django.test import Client

    client = Client(enforce_csrf_checks=True)
    client.force_login(w.peter.user)
    session = client.session
    session["active_membership_id"] = str(w.peter.id)
    session.save()
    response = client.post(
        "/api/sync/push",
        data=json.dumps({"device_id": DEVICE, "mutations": []}),
        content_type="application/json",
    )
    assert response.status_code == 403
    assert response.json()["code"] == "csrf_failed"
