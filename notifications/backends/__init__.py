"""Notification backend selection: docs/02-architecture.md §6, ADR-10.

Channels are chosen through settings (SMS_BACKEND / WHATSAPP_BACKEND), never
by calling a provider directly (CLAUDE.md "Notifications").
"""

from __future__ import annotations

from django.conf import settings

from notifications.backends.base import Channel, NotificationBackend

_instances: dict[str, NotificationBackend] = {}


def get_backend(channel: Channel | str) -> NotificationBackend | None:
    channel = Channel(channel)
    backend_name = settings.SMS_BACKEND if channel == Channel.SMS else settings.WHATSAPP_BACKEND

    if not backend_name or backend_name == "none":
        return None

    if backend_name not in _instances:
        _instances[backend_name] = _build(backend_name)
    return _instances[backend_name]


def _build(name: str) -> NotificationBackend:
    if name == "console":
        from notifications.backends.console import ConsoleBackend

        return ConsoleBackend()
    if name == "stub":
        from notifications.backends.whatsapp_stub import WhatsAppStubBackend

        return WhatsAppStubBackend()
    if name == "africastalking":
        from notifications.backends.africastalking import AfricasTalkingSMSBackend

        return AfricasTalkingSMSBackend()
    raise ValueError(f"Unknown notification backend: {name!r}")
