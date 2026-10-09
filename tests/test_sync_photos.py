"""PUT /api/sync/photos/<uuid> (docs/04-design.md §4.6): the phone uploads a
photo it compressed itself; the server re-encodes it like any other."""

import hashlib
import io
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone
from PIL import Image

from tasks.models import TaskPhoto
from tests.factories import (
    ChecklistRunFactory,
    LocationFactory,
    MembershipFactory,
    OrganisationFactory,
    ShiftAssignmentFactory,
    ShiftFactory,
    TaskFactory,
)

pytestmark = pytest.mark.django_db


def jpeg(colour=(10, 120, 30), size=(800, 600)):
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="JPEG", quality=80)
    return buffer.getvalue()


@pytest.fixture
def w(org_a):
    main = LocationFactory(organisation=org_a)
    peter = MembershipFactory(organisation=org_a, role="staff")
    task = TaskFactory(
        organisation=org_a, location=main, assignee_location=False, assignee_membership=peter
    )
    return SimpleNamespace(org=org_a, main=main, peter=peter, task=task)


def put(client, photo_id, body, parent, content_type="image/jpeg", **headers):
    return client.generic(
        "PUT",
        f"/api/sync/photos/{photo_id}",
        data=body,
        content_type=content_type,
        HTTP_X_PHOTO_PARENT=parent,
        **headers,
    )


def test_upload_for_a_task(w, login_as):
    photo_id, body = uuid.uuid4(), jpeg()
    response = put(
        login_as(w.peter),
        photo_id,
        body,
        f"task:{w.task.id}",
        HTTP_X_TAKEN_AT="2031-10-14T07:00:00Z",
    )
    assert response.status_code == 201
    photo = TaskPhoto.unscoped.get(pk=photo_id)
    assert photo.task == w.task and photo.linked_at is not None
    assert photo.sha256 == hashlib.sha256(body).hexdigest()
    assert photo.bytes <= 200 * 1024


def test_the_same_upload_again_is_fine(w, login_as):
    client, photo_id, body = login_as(w.peter), uuid.uuid4(), jpeg()
    put(client, photo_id, body, f"task:{w.task.id}")
    assert put(client, photo_id, body, f"task:{w.task.id}").status_code == 200
    assert TaskPhoto.unscoped.filter(pk=photo_id).count() == 1


def test_different_bytes_under_the_same_id_is_a_conflict(w, login_as):
    client, photo_id = login_as(w.peter), uuid.uuid4()
    put(client, photo_id, jpeg(), f"task:{w.task.id}")
    response = put(client, photo_id, jpeg(colour=(200, 0, 0)), f"task:{w.task.id}")
    assert response.status_code == 409
    assert response.json()["code"] == "photo_conflict"


def test_too_large(w, login_as):
    response = put(login_as(w.peter), uuid.uuid4(), b"x" * (351 * 1024), f"task:{w.task.id}")
    assert response.status_code == 413


def test_not_a_jpeg(w, login_as):
    response = put(
        login_as(w.peter), uuid.uuid4(), b"...", f"task:{w.task.id}", content_type="image/png"
    )
    assert response.status_code == 415


def test_a_task_you_cannot_see_is_not_found(w, login_as):
    other = TaskFactory(
        organisation=w.org,
        location=w.main,
        assignee_location=False,
        assignee_membership=MembershipFactory(organisation=w.org),
    )
    assert put(login_as(w.peter), uuid.uuid4(), jpeg(), f"task:{other.id}").status_code == 404


def test_another_organisations_task_is_not_found(w, login_as):
    outsider_task = TaskFactory(organisation=OrganisationFactory())
    assert (
        put(login_as(w.peter), uuid.uuid4(), jpeg(), f"task:{outsider_task.id}").status_code == 404
    )


def test_upload_for_a_checklist_run_stays_unlinked_until_a_tick_uses_it(w, login_as):
    shift = ShiftFactory(organisation=w.org, location=w.main)
    ShiftAssignmentFactory(
        organisation=w.org, shift=shift, membership=w.peter, date=timezone.localdate()
    )
    run = ChecklistRunFactory(
        organisation=w.org,
        location=w.main,
        shift=shift,
        shift_date=timezone.localdate(),
        occurrence_start=timezone.now(),
        due_at=timezone.now() + timedelta(hours=1),
    )
    photo_id = uuid.uuid4()
    assert put(login_as(w.peter), photo_id, jpeg(), f"run:{run.id}").status_code == 201
    photo = TaskPhoto.unscoped.get(pk=photo_id)
    assert photo.checklist_run == run and photo.linked_at is None


def test_a_bad_parent_header_is_a_400(w, login_as):
    assert put(login_as(w.peter), uuid.uuid4(), jpeg(), "banana").status_code == 400
