"""The header bell (F5.6): GET the list, POST to mark it seen."""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from notifications.inbox import recent_items
from organisations.activity import mark_notifications_seen


@login_required
def notification_list(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "notifications/_list.html",
        {"items": recent_items(request.membership)},
    )


@login_required
@require_POST
def notifications_seen(request: HttpRequest) -> HttpResponse:
    mark_notifications_seen(request.membership)
    return HttpResponse(status=204)
