"""The sync API (docs/04-design.md §4.2-4.6): me, pull, push, photos.

Session auth with CSRF (the field app is same-origin), per-membership rate
limits, and {"code", "detail"} error bodies (sync.exceptions). Everything
runs in the request's tenant context (TenantMiddleware), so another
organisation's rows can't be read or written.
"""

from __future__ import annotations

import hashlib
import io
from uuid import UUID

from django.db import IntegrityError
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from checklists.models import ChecklistRun
from sync import pull, push
from sync.exceptions import error
from sync.serializers import iso, photo
from tasks.forms import FLAG_REASONS
from tasks.models import Task, TaskPhoto
from tasks.photos import PhotoError, store_task_photo

MAX_PUSH_BYTES = 256 * 1024
MAX_PHOTO_BYTES = 350 * 1024  # the phone targets ~200 KB (docs/02-architecture.md §8)


class SyncView(APIView):
    throttle_classes = [ScopedRateThrottle]


class MeView(SyncView):
    throttle_scope = "sync-pull"

    def get(self, request: Request) -> Response:
        membership, organisation = request.membership, request.organisation
        return Response(
            {
                "membership": {
                    "id": str(membership.id),
                    "role": membership.role,
                    "name": membership.name,
                },
                "organisation": {
                    "id": str(organisation.id),
                    "name": organisation.name,
                    "timezone": organisation.timezone,
                    "flag_reasons": [str(r) for r in FLAG_REASONS],
                },
                "server_time": iso(timezone.now()),
                "min_client_version": "0",
                "window": {
                    "days_back": pull.WINDOW_BACK.days,
                    "days_ahead": pull.WINDOW_AHEAD.days,
                },
            }
        )


class PullView(SyncView):
    throttle_scope = "sync-pull"

    def get(self, request: Request) -> Response:
        try:
            cursor = int(request.query_params.get("cursor") or 0)
            limit = int(request.query_params.get("limit") or pull.DEFAULT_LIMIT)
        except ValueError:
            return error(400, detail="cursor and limit must be integers")
        if cursor < 0 or not 1 <= limit <= pull.MAX_LIMIT:
            return error(400, detail="cursor or limit out of range")
        now = timezone.now()
        data = pull.build_pull(request.membership, cursor, limit, now)
        return Response({**data, "server_time": iso(now)})


class PushView(SyncView):
    throttle_scope = "sync-push"

    def post(self, request: Request) -> Response:
        if int(request.META.get("CONTENT_LENGTH") or 0) > MAX_PUSH_BYTES:
            return error(413, detail="split the batch")
        body = request.data
        if isinstance(body, dict) and len(body.get("mutations") or []) > push.MAX_MUTATIONS:
            return error(413, detail=f"at most {push.MAX_MUTATIONS} mutations per push")
        try:
            device_id, mutations = push.parse(body)
        except push.BadEnvelope as exc:
            return error(400, detail=str(exc))
        now = timezone.now()
        results = push.run_push(request.membership, device_id, mutations, now)
        return Response({"server_time": iso(now), "results": results})


class PhotoView(SyncView):
    """PUT the phone's compressed JPEG. Header X-Photo-Parent: task:<id> or
    run:<id>. Idempotent by id + content hash (201 new, 200 same again,
    409 different bytes under the same id)."""

    throttle_scope = "sync-photos"

    def put(self, request: Request, photo_id: UUID) -> Response:
        if request.content_type != "image/jpeg":
            return error(415, detail="send image/jpeg")
        if int(request.META.get("CONTENT_LENGTH") or 0) > MAX_PHOTO_BYTES:
            return error(413, detail="recompress the photo")
        body = request.body
        if len(body) > MAX_PHOTO_BYTES:
            return error(413, detail="recompress the photo")

        kind, _, raw_id = request.headers.get("X-Photo-Parent", "").partition(":")
        try:
            parent_id = UUID(raw_id)
        except ValueError:
            return error(400, detail="X-Photo-Parent must be task:<uuid> or run:<uuid>")
        if kind == "task":
            parent = Task.objects.visible_to(request.membership).filter(pk=parent_id).first()
        elif kind == "run":
            parent = (
                ChecklistRun.objects.visible_to(request.membership).filter(pk=parent_id).first()
            )
        else:
            return error(400, detail="X-Photo-Parent must be task:<uuid> or run:<uuid>")
        if parent is None:
            return error(404)

        existing = TaskPhoto.objects.filter(pk=photo_id).first()
        if existing is not None:
            if existing.sha256 == hashlib.sha256(body).hexdigest():
                return Response(photo(existing), status=200)
            return error(409, "photo_conflict", "different bytes under this photo id")

        taken_at = parse_datetime(request.headers.get("X-Taken-At", "") or "")
        try:
            stored = store_task_photo(
                uploaded_by=request.membership,
                upload=io.BytesIO(body),
                task=parent if kind == "task" else None,
                checklist_run=parent if kind == "run" else None,
                taken_at=taken_at,
                # A run's photo is claimed by the tick that uses it.
                link=kind == "task",
                photo_id=photo_id,
            )
        except PhotoError as exc:
            return error(400, "validation_error", str(exc))
        except IntegrityError:
            return error(409, "photo_conflict", "this photo id is taken")
        return Response(photo(stored), status=201)
