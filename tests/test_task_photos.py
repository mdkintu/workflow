"""Proof photos: Pillow re-encode to <= ~200 KB with EXIF (incl. GPS)
stripped, a thumbnail, and serving only through a permission-checked view
(docs/02-architecture.md §8, ADR-11)."""

import io
import os
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from tasks import photos
from tasks.models import TaskPhoto
from tests.factories import LocationFactory, MembershipFactory, OrganisationFactory, TaskFactory

pytestmark = pytest.mark.django_db


def _jpeg_with_gps(width=3000, height=2000) -> bytes:
    """Random noise compresses badly, so this is a worst case for the size
    target. The EXIF block carries a GPS tag that must not survive."""
    image = Image.frombytes("RGB", (width, height), os.urandom(width * height * 3))
    exif = Image.Exif()
    exif[0x8825] = {1: "N", 2: (0.0, 20.0, 0.0)}  # GPSInfo IFD
    exif[0x010F] = "PhoneMaker"  # Make
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=95, exif=exif)
    return buffer.getvalue()


def _png(width=400, height=300) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (200, 30, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_compress_hits_the_size_target_and_strips_exif():
    result = photos.compress(io.BytesIO(_jpeg_with_gps()))

    assert len(result.full) <= photos.TARGET_MAX_BYTES
    assert max(result.width, result.height) <= photos.MAX_SIDE
    reopened = Image.open(io.BytesIO(result.full))
    assert reopened.format == "JPEG"
    assert dict(reopened.getexif()) == {}
    thumb = Image.open(io.BytesIO(result.thumb))
    assert max(thumb.size) <= photos.THUMB_SIDE


def test_png_is_accepted_and_converted_to_jpeg():
    result = photos.compress(io.BytesIO(_png()))
    assert Image.open(io.BytesIO(result.full)).format == "JPEG"


def test_a_non_image_is_rejected():
    with pytest.raises(photos.PhotoError):
        photos.compress(io.BytesIO(b"definitely not an image"))


def test_an_oversized_upload_is_rejected(monkeypatch):
    monkeypatch.setattr(photos, "MAX_UPLOAD_BYTES", 100)
    with pytest.raises(photos.PhotoError):
        photos.compress(io.BytesIO(_png()))


@pytest.fixture
def task_world(org_a):
    location = LocationFactory(organisation=org_a)
    staff = MembershipFactory(organisation=org_a, role="staff")
    task = TaskFactory(
        organisation=org_a,
        location=location,
        assignee_location=False,
        assignee_membership=staff,
    )
    return task, staff


def _upload(client, task, data=None, name="photo.jpg"):
    return client.post(
        f"/tasks/{task.id}/photos/",
        {"photo": SimpleUploadedFile(name, data or _png(), content_type="image/jpeg")},
    )


def test_assignee_uploads_a_photo_that_is_stored_and_linked(task_world, login_as, settings):
    task, staff = task_world
    response = _upload(login_as(staff), task)
    assert response.status_code == 302

    photo = TaskPhoto.unscoped.get(task=task)
    assert photo.linked_at is not None
    assert photo.uploaded_by == staff
    assert photo.file.startswith(f"org/{task.organisation_id}/photos/")
    assert (Path(settings.MEDIA_ROOT) / photo.file).exists()
    assert (Path(settings.MEDIA_ROOT) / photo.thumb).exists()


def test_uploading_garbage_shows_an_error_and_stores_nothing(task_world, login_as):
    task, staff = task_world
    response = _upload(login_as(staff), task, data=b"nope", name="x.jpg")
    assert response.status_code == 400
    assert not TaskPhoto.unscoped.filter(task=task).exists()


def test_the_photo_is_served_to_someone_who_can_see_the_task(task_world, login_as):
    task, staff = task_world
    client = login_as(staff)
    _upload(client, task)
    photo = TaskPhoto.unscoped.get(task=task)

    response = client.get(f"/media/p/{photo.id}")
    assert response.status_code == 200
    assert response["Content-Type"] == "image/jpeg"
    assert "private" in response["Cache-Control"]
    assert client.get(f"/media/p/{photo.id}/t").status_code == 200


def test_the_photo_is_hidden_from_staff_who_cannot_see_the_task(task_world, login_as, client):
    task, staff = task_world
    _upload(login_as(staff), task)
    photo = TaskPhoto.unscoped.get(task=task)
    client.logout()

    other_staff = MembershipFactory(organisation=task.organisation, role="staff")
    assert login_as(other_staff).get(f"/media/p/{photo.id}").status_code == 403


def test_the_photo_404s_for_another_organisation(task_world, login_as, client):
    task, staff = task_world
    _upload(login_as(staff), task)
    photo = TaskPhoto.unscoped.get(task=task)
    client.logout()

    outsider = MembershipFactory(organisation=OrganisationFactory(), role="owner")
    assert login_as(outsider).get(f"/media/p/{photo.id}").status_code == 404


def test_behind_caddy_the_file_is_handed_off_with_x_accel_redirect(task_world, login_as, settings):
    task, staff = task_world
    client = login_as(staff)
    _upload(client, task)
    photo = TaskPhoto.unscoped.get(task=task)

    settings.MEDIA_X_ACCEL = True
    settings.MEDIA_INTERNAL_PREFIX = "/_protected/media/"
    response = client.get(f"/media/p/{photo.id}")
    assert response.status_code == 200
    assert response["X-Accel-Redirect"] == f"/_protected/media/{photo.file}"
    assert response.content == b""
