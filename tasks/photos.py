"""Proof photos (docs/02-architecture.md §8, ADR-11).

Every upload is re-encoded by Pillow to a JPEG of at most TARGET_MAX_BYTES
(~200 KB) and MAX_SIDE px, which also drops all metadata — EXIF, including
GPS (docs/01-requirements.md NFR-S5). A small thumbnail is written alongside.
Files live under MEDIA_ROOT at org/<org id>/photos/<photo id>.jpg and are
only ever served through photo_response(), after a permission check.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.http import FileResponse, HttpResponse
from django.utils import timezone
from django.utils.translation import gettext as _
from PIL import Image, ImageOps, UnidentifiedImageError

from organisations.models import Membership
from organisations.tenancy import tenant_context
from tasks.models import Task, TaskPhoto

MAX_UPLOAD_BYTES = 12 * 1024 * 1024  # a raw phone camera photo, before we shrink it
TARGET_MAX_BYTES = 200 * 1024
MAX_SIDE = 1280
THUMB_SIDE = 240
ACCEPTED_FORMATS = {"JPEG", "MPO", "PNG", "WEBP"}  # MPO: some Android cameras' JPEGs

# Tried in order until the result fits TARGET_MAX_BYTES. Real photos almost
# always fit at the first side/quality; the rest is for worst cases.
_SIDES = (MAX_SIDE, 1024, 800, 640, 480)
_QUALITIES = (80, 70, 60, 50, 40)


class PhotoError(Exception):
    """A user-facing reason the upload can't be used."""


@dataclass(frozen=True)
class Compressed:
    full: bytes
    thumb: bytes
    width: int
    height: int
    source_sha256: str  # of the bytes as uploaded: what makes a re-upload idempotent


def compress(fileobj) -> Compressed:
    data = fileobj.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise PhotoError(_("That photo is too large. Try again with a smaller one."))

    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
        image = Image.open(io.BytesIO(data))
        source_format = image.format
        image.load()
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise PhotoError(_("That file isn't a photo we can read.")) from exc
    if source_format not in ACCEPTED_FORMATS:
        raise PhotoError(_("Please use a JPEG, PNG or WebP photo."))

    image = ImageOps.exif_transpose(image).convert("RGB")

    full, size = b"", image.size
    candidate = image
    for side in _SIDES:
        candidate = candidate.copy()
        candidate.thumbnail((side, side), Image.Resampling.LANCZOS)
        for quality in _QUALITIES:
            full, size = _jpeg(candidate, quality), candidate.size
            if len(full) <= TARGET_MAX_BYTES:
                break
        if len(full) <= TARGET_MAX_BYTES:
            break

    thumb = candidate.copy()
    thumb.thumbnail((THUMB_SIDE, THUMB_SIDE), Image.Resampling.LANCZOS)
    return Compressed(
        full=full,
        thumb=_jpeg(thumb, 70),
        width=size[0],
        height=size[1],
        source_sha256=hashlib.sha256(data).hexdigest(),
    )


def _jpeg(image: Image.Image, quality: int) -> bytes:
    image.info = {}  # never carry EXIF/ICC/comments through to the output
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality, optimize=True, progressive=True)
    return buffer.getvalue()


def _storage() -> FileSystemStorage:
    return FileSystemStorage(location=settings.MEDIA_ROOT)


def store_task_photo(
    *,
    uploaded_by: Membership,
    upload,
    task: Task | None = None,
    checklist_run=None,
    taken_at: datetime | None = None,
    link: bool = True,
    photo_id=None,
) -> TaskPhoto:
    """Compresses `upload` and attaches it to a task (proof) or a checklist
    run (an item's photo). Raises PhotoError (nothing is stored) if the file
    can't be used. With link=False the photo stays unlinked until whatever
    it's for claims it (a checklist tick); unlinked photos are orphans for
    the cleanup job (docs/02-architecture.md §5)."""
    parent = task if task is not None else checklist_run
    if parent is None or parent.organisation_id != uploaded_by.organisation_id:
        raise PhotoError(_("This task was not found."))

    compressed = compress(upload)
    photo_id = photo_id or uuid4()  # the phone generates it for offline photos
    base = f"org/{parent.organisation_id}/photos/{photo_id}"
    storage = _storage()
    full_name = storage.save(f"{base}.jpg", ContentFile(compressed.full))
    thumb_name = storage.save(f"{base}_t.jpg", ContentFile(compressed.thumb))

    with tenant_context(uploaded_by.organisation):
        return TaskPhoto.objects.create(
            id=photo_id,
            task=task,
            checklist_run=checklist_run,
            file=full_name,
            thumb=thumb_name,
            bytes=len(compressed.full),
            sha256=compressed.source_sha256,
            width=compressed.width,
            height=compressed.height,
            taken_at_device=taken_at,
            uploaded_by=uploaded_by,
            linked_at=timezone.now() if link else None,
        )


def photo_response(photo: TaskPhoto, *, thumb: bool = False) -> HttpResponse:
    """The caller has already checked the viewer may see this photo. Behind
    Caddy (MEDIA_X_ACCEL) Django only returns a header and Caddy streams the
    file (docs/02-architecture.md §7); in dev mode Django streams it itself."""
    name = photo.thumb if thumb else photo.file
    if settings.MEDIA_X_ACCEL:
        response = HttpResponse(content_type="image/jpeg")
        response["X-Accel-Redirect"] = f"{settings.MEDIA_INTERNAL_PREFIX}{name}"
    else:
        path = Path(settings.MEDIA_ROOT) / name
        response = FileResponse(path.open("rb"), content_type="image/jpeg")
    response["Cache-Control"] = "private, max-age=86400"
    return response
