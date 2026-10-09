"""Africa's Talking SMS backend (ADR-10). A minimal urllib POST — no SDK, no
network calls in tests (they use ConsoleBackend / SMS_BACKEND=console).
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

from notifications.backends.base import Channel, OutboundMessage, SendResult

logger = logging.getLogger(__name__)

_AT_SUCCESS_STATUS_CODE = 101


class AfricasTalkingSMSBackend:
    channels = frozenset({Channel.SMS})

    def send(self, message: OutboundMessage) -> SendResult:
        data = urllib.parse.urlencode(
            {
                "username": settings.AT_USERNAME,
                "to": message.to,
                "message": message.body,
                "from": settings.AT_SENDER_ID,
            }
        ).encode("utf-8")
        request = urllib.request.Request(  # noqa: S310 - fixed, config-driven URL
            settings.AT_API_URL,
            data=data,
            headers={
                "apikey": settings.AT_API_KEY,
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError) as exc:
            logger.warning("Africa's Talking send failed: %s", exc.__class__.__name__)
            return SendResult(
                accepted=False,
                provider_message_id=None,
                status="failed",
                cost=None,
                error="network_error",
                retryable=True,
            )

        recipients = body.get("SMSMessageData", {}).get("Recipients", [])
        recipient = recipients[0] if recipients else {}
        status_code = recipient.get("statusCode")

        if status_code == _AT_SUCCESS_STATUS_CODE:
            return SendResult(
                accepted=True,
                provider_message_id=recipient.get("messageId"),
                status="sent",
                cost=None,
                error=None,
                retryable=False,
            )
        return SendResult(
            accepted=False,
            provider_message_id=recipient.get("messageId"),
            status="failed",
            cost=None,
            error=recipient.get("status", "unknown_error"),
            retryable=status_code not in (401, 403),
        )
