"""Dev/test backend: logs (without the phone number or body — CLAUDE.md
"Privacy") and keeps an in-memory outbox tests can inspect directly.
"""

from __future__ import annotations

import logging

from notifications.backends.base import Channel, OutboundMessage, SendResult

logger = logging.getLogger(__name__)

outbox: list[OutboundMessage] = []


class ConsoleBackend:
    channels = frozenset({Channel.SMS, Channel.WHATSAPP})

    def send(self, message: OutboundMessage) -> SendResult:
        logger.info(
            "console notification dispatched: channel=%s dedupe_key=%s",
            message.channel,
            message.dedupe_key,
        )
        outbox.append(message)
        return SendResult(
            accepted=True,
            provider_message_id=f"console-{len(outbox)}",
            status="sent",
            cost=None,
            error=None,
            retryable=False,
        )
