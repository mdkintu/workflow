"""F1.6 task board (ADR-21): the tasks list as four status columns, with a
"Next" move that only uses the existing transitions."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from tasks.models import Task, TaskStatus
from tests.factories import LocationFactory, MembershipFactory, TaskFactory

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture
def w(org_a):
    location = LocationFactory(organisation=org_a, name="Main building")
    world = SimpleNamespace(
        org=org_a,
        location=location,
        manager=MembershipFactory(organisation=org_a, role="manager"),
        staff=MembershipFactory(organisation=org_a, role="staff", user__name="Peter Okello"),
    )

    def task(title, status=TaskStatus.PENDING, photo_required=False):
        return TaskFactory(
            organisation=org_a,
            location=location,
            assignee_location=False,
            assignee_membership=world.staff,
            created_by=world.manager,
            photo_required=photo_required,
            title=title,
            status=status,
            due_at=timezone.now() + timedelta(hours=1),
        )

    world.task = task
    return world


def _cards(response):
    return response.content.decode().count('data-testid="board-card"')


def test_f1_6_board_shows_the_four_status_columns(w, login_as):
    w.task("Wipe tables")
    w.task("Mop floor", TaskStatus.IN_PROGRESS)
    w.task("Old job", TaskStatus.CANCELLED)

    response = login_as(w.manager).get("/tasks/?view=board")

    body = response.content.decode()
    assert response.status_code == 200
    for label in ("Pending", "In progress", "Flagged", "Done"):
        assert label in body
    assert _cards(response) == 2  # cancelled work isn't on the board
    assert "Old job" not in body


def test_f1_6_next_starts_a_pending_task_and_returns_to_the_board(w, login_as):
    task = w.task("Wipe tables")
    client = login_as(w.manager)

    response = client.get("/tasks/?view=board")
    assert f'action="/tasks/{task.id}/start/"' in response.content.decode()

    response = client.post(f"/tasks/{task.id}/start/", {"next": "/tasks/?view=board"})

    assert response.status_code == 302
    assert response.url == "/tasks/?view=board"
    assert Task.unscoped.get(pk=task.pk).status == TaskStatus.IN_PROGRESS


def test_f1_6_next_ignores_an_off_site_redirect(w, login_as):
    task = w.task("Wipe tables")
    response = login_as(w.manager).post(
        f"/tasks/{task.id}/start/", {"next": "https://example.com/phish"}
    )
    assert response.url == f"/tasks/{task.id}/"


def test_f1_6_photo_tasks_are_finished_on_the_task_page(w, login_as):
    task = w.task("Clean room", TaskStatus.IN_PROGRESS, photo_required=True)
    body = login_as(w.manager).get("/tasks/?view=board").content.decode()
    assert f'action="/tasks/{task.id}/complete/"' not in body
    assert "Add photo" in body


def test_f1_6_done_cards_offer_send_back_through_the_task_page(w, login_as):
    w.task("Wiped", TaskStatus.DONE)
    body = login_as(w.manager).get("/tasks/?view=board").content.decode()
    assert "Send back" in body
    assert "/reject/" not in body  # a reason is needed, so it's never a one-tap POST


def test_f1_6_htmx_filters_swap_only_the_board(w, login_as):
    w.task("Wipe tables")
    response = login_as(w.manager).get("/tasks/?view=board", **HTMX)
    body = response.content.decode()
    assert body.lstrip().startswith('<div id="task-board"')
    assert "<html" not in body


def test_f1_6_staff_cannot_open_the_board(w, login_as):
    assert login_as(w.staff).get("/tasks/?view=board").status_code == 403
