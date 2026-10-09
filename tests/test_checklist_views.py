"""Checklist screens (docs/04-design.md §3 S6, §4.1 /checklists/...): the
template editor (items, schedules), today's runs, the run page, and runs
showing up in My tasks."""

import io
from datetime import time, timedelta
from types import SimpleNamespace

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from PIL import Image

from checklists.models import (
    ChecklistItem,
    ChecklistRun,
    ChecklistRunItemTick,
    ChecklistTemplate,
    RecurrenceRule,
)
from organisations.models import AuditEvent
from tasks.models import TaskPhoto
from tests.factories import (
    ChecklistItemFactory,
    ChecklistRunFactory,
    ChecklistRunItemFactory,
    ChecklistTemplateFactory,
    LocationFactory,
    MembershipFactory,
    OrganisationFactory,
    ShiftAssignmentFactory,
    ShiftFactory,
)

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}


def _jpeg():
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), (10, 120, 30)).save(buffer, format="JPEG")
    return SimpleUploadedFile("p.jpg", buffer.getvalue(), content_type="image/jpeg")


# --- template editor (Manager+) ---


def test_a_supervisor_cannot_manage_templates(org_a, login_as):
    supervisor = MembershipFactory(organisation=org_a, role="supervisor")
    assert login_as(supervisor).get("/checklists/").status_code == 403


def test_a_manager_creates_a_template(org_a, manager_membership, login_as):
    location = LocationFactory(organisation=org_a)
    response = login_as(manager_membership).post(
        "/checklists/new/", {"name": "Closing — Bar", "location": str(location.id), "shift": ""}
    )
    template = ChecklistTemplate.unscoped.get(name="Closing — Bar")
    assert response.status_code == 302
    assert response.url == f"/checklists/{template.id}/"
    assert template.created_by == manager_membership
    assert AuditEvent.unscoped.filter(action="template.create", target_id=template.id).exists()


def test_a_shift_at_another_location_is_rejected(org_a, manager_membership, login_as):
    location = LocationFactory(organisation=org_a)
    shift = ShiftFactory(organisation=org_a, location=LocationFactory(organisation=org_a))
    login_as(manager_membership).post(
        "/checklists/new/",
        {"name": "Closing", "location": str(location.id), "shift": str(shift.id)},
    )
    assert not ChecklistTemplate.unscoped.filter(name="Closing").exists()


@pytest.fixture
def template(org_a, manager_membership):
    return ChecklistTemplateFactory(
        organisation=org_a,
        location=LocationFactory(organisation=org_a),
        created_by=manager_membership,
        name="Opening",
    )


def test_adding_reordering_and_removing_items(org_a, manager_membership, template, login_as):
    client = login_as(manager_membership)
    base = f"/checklists/{template.id}"
    client.post(f"{base}/items/", {"label": "Unlock stores"}, **HTMX)
    client.post(f"{base}/items/", {"label": "Count float", "photo_required": "on"}, **HTMX)
    first, second = ChecklistItem.unscoped.filter(template=template).order_by("order")
    assert second.photo_required and second.skippable

    client.post(f"{base}/items/{second.id}/move/", {"direction": "up"}, **HTMX)
    assert list(
        ChecklistItem.unscoped.filter(template=template)
        .order_by("order")
        .values_list("label", flat=True)
    ) == ["Count float", "Unlock stores"]

    response = client.post(f"{base}/items/{first.id}/remove/", **HTMX)
    assert response.status_code == 200
    first.refresh_from_db()
    assert first.is_active is False


def test_adding_a_schedule_generates_runs_straight_away(
    org_a, manager_membership, template, login_as
):
    ChecklistItemFactory(organisation=org_a, template=template)
    later_today = (
        (timezone.localtime() + timedelta(hours=2)).time().replace(second=0, microsecond=0)
    )
    response = login_as(manager_membership).post(
        f"/checklists/{template.id}/rules/",
        {
            "kind": "daily",
            "times": later_today.strftime("%H:%M"),
            "due_offset_min": "60",
            "available_before_min": "0",
            "starts_on": timezone.localdate().isoformat(),
        },
    )
    assert response.status_code == 302
    assert RecurrenceRule.unscoped.filter(template=template).exists()
    assert ChecklistRun.unscoped.filter(template=template).exists()


def test_a_shift_start_schedule_needs_a_shift_template(
    org_a, manager_membership, template, login_as
):
    login_as(manager_membership).post(
        f"/checklists/{template.id}/rules/",
        {
            "kind": "shift_start",
            "times": "",
            "due_offset_min": "60",
            "available_before_min": "0",
            "starts_on": timezone.localdate().isoformat(),
        },
    )
    assert not RecurrenceRule.unscoped.filter(template=template).exists()


def test_another_organisations_template_is_a_404(org_a, template, login_as):
    outsider = MembershipFactory(organisation=OrganisationFactory(), role="owner")
    assert login_as(outsider).get(f"/checklists/{template.id}/").status_code == 404


def test_the_template_list_shows_only_this_organisation(
    org_a, org_b, template, manager_membership, login_as
):
    ChecklistTemplateFactory(organisation=org_b, name="Someone else's")
    body = login_as(manager_membership).get("/checklists/").content.decode()
    assert "Opening" in body
    assert "Someone else" not in body


# --- runs ---


@pytest.fixture
def w(org_a):
    location = LocationFactory(organisation=org_a)
    shift = ShiftFactory(organisation=org_a, location=location, start_time=time(6, 0))
    today = timezone.localdate()
    world = SimpleNamespace(
        org=org_a,
        staff=MembershipFactory(organisation=org_a, role="staff"),
        other_staff=MembershipFactory(organisation=org_a, role="staff"),
        supervisor=MembershipFactory(organisation=org_a, role="supervisor"),
    )
    ShiftAssignmentFactory(organisation=org_a, shift=shift, membership=world.staff, date=today)
    world.run = ChecklistRunFactory(
        organisation=org_a,
        name="Opening checklist — Bar",
        location=location,
        shift=shift,
        shift_date=today,
        occurrence_start=timezone.now() - timedelta(minutes=5),
        due_at=timezone.now() + timedelta(hours=1),
    )
    world.item = ChecklistRunItemFactory(organisation=org_a, run=world.run, order=1, label="Unlock")
    world.photo_item = ChecklistRunItemFactory(
        organisation=org_a, run=world.run, order=2, label="Fridges", photo_required=True
    )
    return world


def test_rostered_staff_open_the_run(w, login_as):
    response = login_as(w.staff).get(f"/checklists/runs/{w.run.id}/")
    assert response.status_code == 200
    assert b"Unlock" in response.content


def test_staff_not_rostered_get_403_and_other_orgs_404(w, login_as, client):
    assert login_as(w.other_staff).get(f"/checklists/runs/{w.run.id}/").status_code == 403
    client.logout()
    outsider = MembershipFactory(organisation=OrganisationFactory(), role="owner")
    assert login_as(outsider).get(f"/checklists/runs/{w.run.id}/").status_code == 404


def test_ticking_over_htmx(w, login_as):
    response = login_as(w.staff).post(f"/checklists/items/{w.item.id}/tick/", **HTMX)
    assert response.status_code == 200
    assert ChecklistRunItemTick.unscoped.filter(run_item=w.item, membership=w.staff).exists()


def test_ticking_a_photo_item_with_a_photo(w, login_as):
    login_as(w.staff).post(f"/checklists/items/{w.photo_item.id}/tick/", {"photo": _jpeg()}, **HTMX)
    tick = ChecklistRunItemTick.unscoped.get(run_item=w.photo_item)
    assert tick.photo is not None
    assert tick.photo.checklist_run_id == w.run.id


def test_ticking_a_photo_item_without_a_photo_explains_why(w, login_as):
    response = login_as(w.staff).post(f"/checklists/items/{w.photo_item.id}/tick/", **HTMX)
    assert b"Add a photo" in response.content
    assert not ChecklistRunItemTick.unscoped.filter(run_item=w.photo_item).exists()


def test_skipping_over_htmx(w, login_as):
    login_as(w.staff).post(
        f"/checklists/items/{w.item.id}/skip/", {"reason": "Machine broken"}, **HTMX
    )
    assert ChecklistRunItemTick.unscoped.get(run_item=w.item).skipped


def test_other_staff_cannot_tick(w, login_as):
    response = login_as(w.other_staff).post(f"/checklists/items/{w.item.id}/tick/", **HTMX)
    assert response.status_code == 403


def test_a_run_photo_is_only_served_to_people_who_can_see_the_run(w, login_as, client):
    login_as(w.staff).post(f"/checklists/items/{w.photo_item.id}/tick/", {"photo": _jpeg()}, **HTMX)
    photo = TaskPhoto.unscoped.get(checklist_run=w.run)
    assert client.get(f"/media/p/{photo.id}").status_code == 200
    client.logout()
    assert login_as(w.other_staff).get(f"/media/p/{photo.id}").status_code == 403


def test_runs_appear_in_my_tasks_with_progress(w, login_as):
    ChecklistRunItemTick.unscoped.create(
        organisation=w.org,
        run_item=w.item,
        membership=w.staff,
        device_time=timezone.now(),
        trusted_time=timezone.now(),
    )
    body = login_as(w.staff).get("/tasks/my/").content.decode()
    assert "Opening checklist — Bar" in body
    assert "1 / 2" in body


def test_supervisors_see_the_days_runs(w, login_as):
    # The run's own local due day (it's due in an hour, which may be tomorrow).
    day = timezone.localtime(w.run.due_at).date().isoformat()
    body = login_as(w.supervisor).get(f"/checklists/runs/?day={day}").content.decode()
    assert "Opening checklist — Bar" in body


def test_staff_cannot_open_the_runs_list(w, login_as):
    assert login_as(w.staff).get("/checklists/runs/").status_code == 403


def test_reporting_a_problem_on_a_run(w, login_as):
    login_as(w.staff).post(
        f"/checklists/runs/{w.run.id}/flag/", {"reason": "No supplies", "note": ""}, **HTMX
    )
    w.run.refresh_from_db()
    assert w.run.status == "flagged"


def test_commenting_on_a_run(w, login_as):
    response = login_as(w.staff).post(
        f"/checklists/runs/{w.run.id}/comments/", {"body": "Ice machine is noisy"}, **HTMX
    )
    assert b"Ice machine is noisy" in response.content
