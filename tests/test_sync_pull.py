"""GET /api/sync/pull (docs/04-design.md §4.4, ADR-03): what a phone gets,
the cursor, tombstones, paging, the safe high-water mark and resets."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from organisations.models import Membership
from sync import pull
from tasks.models import Task, TaskComment
from tests.factories import (
    ChecklistRunFactory,
    ChecklistRunItemFactory,
    ChecklistRunItemTickFactory,
    LocationFactory,
    MembershipFactory,
    OrganisationFactory,
    ShiftAssignmentFactory,
    ShiftFactory,
    TaskCommentFactory,
    TaskFactory,
    TaskPhotoFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_lag(monkeypatch):
    """Rows written by the test itself are "just now"; the lag is tested on
    its own below."""
    monkeypatch.setattr(pull, "LAG", timedelta(0))


@pytest.fixture
def w(org_a):
    now = timezone.now()
    main = LocationFactory(organisation=org_a, name="Main building")
    shift = ShiftFactory(organisation=org_a, location=main, name="Morning")
    peter = MembershipFactory(organisation=org_a, role="staff", user__name="Peter")
    grace = MembershipFactory(organisation=org_a, role="staff", user__name="Grace")
    ShiftAssignmentFactory(
        organisation=org_a, shift=shift, membership=peter, date=timezone.localdate()
    )

    def task(**kw):
        kw.setdefault("assignee_location", False)
        kw.setdefault("assignee_membership", peter)
        kw.setdefault("due_at", now + timedelta(hours=2))
        return TaskFactory(organisation=org_a, location=main, **kw)

    world = SimpleNamespace(
        org=org_a, now=now, main=main, shift=shift, peter=peter, grace=grace, task=task
    )
    world.mine = task(title="Mine")
    world.graces = task(title="Grace's", assignee_membership=grace)
    world.old = task(title="Last week", due_at=now - timedelta(days=5))
    world.later = task(title="Next week", due_at=now + timedelta(days=5))
    world.run = ChecklistRunFactory(
        organisation=org_a,
        location=main,
        shift=shift,
        shift_date=timezone.localdate(),
        name="Opening",
        occurrence_start=now,
        due_at=now + timedelta(hours=1),
    )
    world.item = ChecklistRunItemFactory(organisation=org_a, run=world.run, order=1)
    world.tick = ChecklistRunItemTickFactory(
        organisation=org_a, run_item=world.item, membership=peter
    )
    world.comment = TaskCommentFactory(organisation=org_a, task=world.mine, author=grace, body="Hi")
    world.photo = TaskPhotoFactory(
        organisation=org_a, task=world.mine, uploaded_by=peter, linked_at=now
    )
    return world


def _pull(client, cursor=0, **params):
    response = client.get("/api/sync/pull", {"cursor": cursor, **params})
    assert response.status_code == 200, response.content
    return response.json()


def _ids(data, table):
    return {row["id"] for row in data["changes"][table]}


def test_a_full_pull_has_my_window_and_nothing_else(w, login_as):
    data = _pull(login_as(w.peter))
    assert _ids(data, "tasks") == {str(w.mine.id)}
    assert _ids(data, "runs") == {str(w.run.id)}
    assert _ids(data, "run_items") == {str(w.item.id)}
    assert _ids(data, "run_item_ticks") == {str(w.tick.id)}
    assert _ids(data, "comments") == {str(w.comment.id)}
    assert _ids(data, "photos") == {str(w.photo.id)}
    assert str(w.main.id) in _ids(data, "locations")  # reference data: the whole organisation
    assert {"Peter", "Grace"} <= {p["name"] for p in data["changes"]["people"]}
    assert len(data["changes"]["shift_assignments"]) == 1
    assert data["has_more"] is False and data["reset"] is False
    assert isinstance(data["cursor"], int) and data["cursor"] > 0


def test_the_task_row_has_what_the_phone_needs(w, login_as):
    row = _pull(login_as(w.peter))["changes"]["tasks"][0]
    assert row["title"] == "Mine"
    assert row["assignee"] == {"type": "membership", "id": str(w.peter.id)}
    assert row["status"] == "pending"
    assert row["due_at"].endswith("Z")
    assert "organisation_id" not in row  # the phone never sends or needs it


def test_a_delta_pull_has_only_what_changed(w, login_as):
    client = login_as(w.peter)
    cursor = _pull(client)["cursor"]
    assert all(not rows for rows in _pull(client, cursor)["changes"].values())

    Task.unscoped.filter(pk=w.mine.pk).update(title="Mine, renamed")
    data = _pull(client, cursor)
    assert [r["title"] for r in data["changes"]["tasks"]] == ["Mine, renamed"]
    assert data["cursor"] > cursor


def test_a_task_reassigned_away_becomes_a_tombstone(w, login_as):
    client = login_as(w.peter)
    cursor = _pull(client)["cursor"]
    Task.unscoped.filter(pk=w.mine.pk).update(assignee_membership=w.grace)
    data = _pull(client, cursor)
    assert {"type": "tasks", "id": str(w.mine.id)} in data["tombstones"]
    assert _ids(data, "tasks") == set()


def test_a_deleted_comment_becomes_a_tombstone(w, login_as):
    client = login_as(w.peter)
    cursor = _pull(client)["cursor"]
    TaskComment.unscoped.filter(pk=w.comment.pk).update(deleted_at=timezone.now())
    assert {"type": "comments", "id": str(w.comment.id)} in _pull(client, cursor)["tombstones"]


def test_a_task_that_becomes_mine_arrives_with_its_older_comments(w, login_as):
    client = login_as(w.peter)
    old_comment = TaskCommentFactory(
        organisation=w.org, task=w.graces, author=w.grace, body="Before"
    )
    cursor = _pull(client)["cursor"]
    Task.unscoped.filter(pk=w.graces.pk).update(assignee_membership=w.peter)
    data = _pull(client, cursor)
    assert str(w.graces.id) in _ids(data, "tasks")
    assert str(old_comment.id) in _ids(data, "comments")


def test_paging_covers_everything_exactly_once(w, login_as):
    for i in range(5):
        w.task(title=f"Extra {i}")
    client = login_as(w.peter)
    seen, cursor, pages = [], 0, 0
    while True:
        data = _pull(client, cursor, limit=2)
        seen += [r["id"] for r in data["changes"]["tasks"]]
        cursor, pages = data["cursor"], pages + 1
        if not data["has_more"]:
            break
    assert pages > 1
    assert len(seen) == len(set(seen)) == 6


def test_rows_written_in_the_last_few_seconds_wait_for_the_next_pull(w, login_as, monkeypatch):
    """ADR-03's safe high-water mark: a row younger than LAG — and every row
    after it — is left for the next pull, so a slower transaction that
    committed an earlier sequence number can't be skipped."""
    client = login_as(w.peter)
    cursor = _pull(client)["cursor"]
    monkeypatch.setattr(pull, "LAG", timedelta(seconds=10))
    Task.unscoped.filter(pk=w.mine.pk).update(title="Just now", updated_at=timezone.now())
    data = _pull(client, cursor)
    assert _ids(data, "tasks") == set()
    assert data["cursor"] == cursor

    Task.unscoped.filter(pk=w.mine.pk).update(updated_at=timezone.now() - timedelta(seconds=11))
    assert [r["title"] for r in _pull(client, cursor)["changes"]["tasks"]] == ["Just now"]


def test_being_added_to_a_shift_brings_that_shifts_older_work(w, login_as):
    """The shift's tasks were written before the cursor, so paging alone
    would never send them; they come along with the new roster row."""
    tomorrow = timezone.localdate() + timedelta(days=1)
    shift_task = w.task(
        title="Tomorrow's shift",
        assignee_membership=None,
        assignee_shift=w.shift,
        shift_date=tomorrow,
    )
    client = login_as(w.peter)
    cursor = _pull(client)["cursor"]
    assert str(shift_task.id) not in _ids(_pull(client), "tasks")

    ShiftAssignmentFactory(organisation=w.org, shift=w.shift, membership=w.peter, date=tomorrow)
    data = _pull(client, cursor)
    assert data["reset"] is False
    assert str(shift_task.id) in _ids(data, "tasks")


def test_a_first_sync_with_a_shift_today_pages_to_the_end(w, login_as):
    """Regression: my own roster row being newer than the cursor mid-way
    through the first sync must not restart it."""
    client = login_as(w.peter)
    cursor, tasks = 0, set()
    for _ in range(50):
        data = _pull(client, cursor, limit=1)
        assert data["reset"] is False
        tasks |= _ids(data, "tasks")
        cursor = data["cursor"]
        if not data["has_more"]:
            break
    assert tasks == {str(w.mine.id)}


def test_a_cursor_from_the_future_asks_for_a_full_refresh(w, login_as):
    """E.g. the server was restored from a backup."""
    assert _pull(login_as(w.peter), cursor=10**12)["reset"] is True


def test_another_organisation_never_appears(w, login_as):
    outsider = MembershipFactory(organisation=OrganisationFactory(), role="owner")
    data = _pull(login_as(outsider))
    assert all(not rows for table, rows in data["changes"].items() if table != "people")
    assert {p["id"] for p in data["changes"]["people"]} == {str(outsider.id)}


def test_not_logged_in_is_a_401(client):
    response = client.get("/api/sync/pull")
    assert response.status_code == 401
    assert response.json()["code"] == "not_authenticated"


def test_me(w, login_as):
    data = login_as(w.peter).get("/api/sync/me").json()
    assert data["membership"]["id"] == str(w.peter.id)
    assert data["organisation"]["timezone"] == "Africa/Kampala"
    assert data["server_time"].endswith("Z")


def test_inactive_people_still_arrive_so_old_comments_keep_their_author(w, login_as):
    Membership.unscoped.filter(pk=w.grace.pk).update(is_active=False)
    people = _pull(login_as(w.peter))["changes"]["people"]
    assert {"id": str(w.grace.id), "name": "Grace", "role": "staff", "is_active": False} in people
