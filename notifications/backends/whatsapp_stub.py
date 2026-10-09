"""WhatsApp placeholder until docs/01-requirements.md Q3 (which WhatsApp
provider) is answered. It never sends: it reports a permanent failure, so
notifications.dispatch falls back to SMS for anyone who prefers WhatsApp
(docs/02-architecture.md §6)."""

from __future__ import annotations

from notifications.backends.base import Channel, OutboundMessage, SendResult


class WhatsAppStubBackend:
    channels = frozenset({Channel.WHATSAPP})

    def send(self, message: OutboundMessage) -> SendResult:
        return SendResult(
            accepted=False,
            provider_message_id=None,
            status="failed",
            cost=None,
            error="whatsapp_not_configured",
            retryable=False,
        )
