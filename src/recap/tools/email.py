"""Deterministic sandbox email capability for ReCAP side-effect tests."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from email.utils import parseaddr
from typing import Any

from langchain_core.tools import tool


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SandboxMessage:
    message_id: str
    recipient: str
    subject: str
    body: str


class SandboxMailbox:
    """Local-only transport with no network or SMTP execution path."""

    def __init__(self) -> None:
        self.messages: list[SandboxMessage] = []

    def snapshot(self) -> dict[str, Any]:
        fingerprints = [
            _digest(f"{item.message_id}\0{item.recipient}\0{item.subject}\0{item.body}")
            for item in self.messages
        ]
        return {
            "mailbox_message_count": len(self.messages),
            "mailbox_hash": _digest("\n".join(fingerprints)),
        }

    def send(self, recipient: str, subject: str, body: str) -> dict[str, Any]:
        _validate_message(recipient, subject, body)
        message = SandboxMessage(
            message_id=f"sandbox-{uuid.uuid4().hex}",
            recipient=recipient,
            subject=subject,
            body=body,
        )
        self.messages.append(message)
        return {
            "accepted": True,
            "message_id": message.message_id,
            "recipient": recipient,
            "recipient_digest": _digest(recipient),
            "subject_digest": _digest(subject),
            "content_digest": _digest(body),
            "transport": "sandbox",
            "receipt_type": "sandbox_email_receipt",
        }

    def clear(self) -> None:
        self.messages.clear()


def _validate_message(recipient: str, subject: str, body: str) -> None:
    if "\r" in recipient or "\n" in recipient:
        raise ValueError("recipient contains a header injection sequence")
    _, parsed = parseaddr(recipient)
    if parsed != recipient or not recipient.lower().endswith("@example.test"):
        raise ValueError("sandbox recipient must be one address under example.test")
    if "\r" in subject or "\n" in subject:
        raise ValueError("subject contains a header injection sequence")
    if not subject or len(subject) > 998:
        raise ValueError("subject length must be between 1 and 998 characters")
    if not body or len(body) > 100_000:
        raise ValueError("body length must be between 1 and 100000 characters")


SANDBOX_MAILBOX = SandboxMailbox()


class SandboxEmailObserver:
    def snapshot(self) -> dict[str, Any]:
        return SANDBOX_MAILBOX.snapshot()

    def effects(self, before, after, tool_return):
        if before == after or not isinstance(tool_return, dict):
            return [], [], None
        receipt = {
            key: tool_return[key]
            for key in (
                "message_id",
                "recipient_digest",
                "subject_digest",
                "content_digest",
            )
            if key in tool_return
        }
        return (
            ["sandbox_email_sent"],
            ["sandbox_email_receipt", "message_id"],
            {"before": before, "after": after, "effect_receipt": receipt},
        )


@tool
async def send_email(recipient: str, subject: str, body: str) -> dict[str, Any]:
    """Send one plain-text message to the local sandbox mailbox."""
    return SANDBOX_MAILBOX.send(recipient, subject, body)


send_email.metadata = {
    "recap_effect_observer": SandboxEmailObserver(),
    "recap_observed_effects": ["sandbox_email_sent"],
    "recap_state_snapshot": SANDBOX_MAILBOX.snapshot,
    "recap_effect_evidence": ["sandbox_email_receipt", "message_id"],
    "recap_effect_receipt_fields": [
        "message_id",
        "recipient_digest",
        "subject_digest",
        "content_digest",
    ],
}

EMAIL_TOOLS = [send_email]
