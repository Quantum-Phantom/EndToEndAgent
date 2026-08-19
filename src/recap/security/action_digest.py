"""Canonical binding between an approved action and the executed action."""

from __future__ import annotations

from typing import Any

import orjson
from blake3 import blake3

from recap.contracts import RuntimeContract


def calculate_action_digest(
    contract: RuntimeContract,
    candidate: dict[str, Any],
) -> str:
    payload = {
        "contract_id": contract.contract_id,
        "certificate_id": contract.certificate.certificate_id,
        "tool_call_id": candidate.get("id"),
        "tool_name": candidate.get("name"),
        "args": candidate.get("args", {}),
    }
    canonical = orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)
    return blake3(canonical).hexdigest()