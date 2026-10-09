"""Notification provider interface (design sketch in docs/02 §6, ADR-10)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Protocol
from uuid import UUID


class Channel(StrEnum):
    SMS = "sms"
    WHATSAPP = "whatsapp"


@dataclass(frozen=True)
class OutboundMessage:
    to: str  # E.164, e.g. "+256772123456"
    body: str  # <= 160 GSM-7 chars for SMS
    channel: Channel
    dedupe_key: str
    organisation_id: UUID


@dataclass(frozen=True)
class SendResult:
    accepted: bool
    provider_message_id: str | None
    status: Literal["queued", "sent", "failed"]
    cost: Decimal | None
    error: str | None
    retryable: bool


class NotificationBackend(Protocol):
    channels: frozenset[Channel]

    def send(self, message: OutboundMessage) -> SendResult: ...
