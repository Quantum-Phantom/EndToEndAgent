"""Trusted capability declarations for registered tools."""

from recap.contracts import ToolCapability
from recap.schemas import DataSource, TrustLevel


ARITHMETIC_CAPABILITIES: dict[str, ToolCapability] = {
    "add": ToolCapability(
        name="add",
        argument_constraints={
            "a": {"gte": -1_000_000, "lte": 1_000_000},
            "b": {"gte": -1_000_000, "lte": 1_000_000},
        },
        required_permissions=["arithmetic:execute"],
        allowed_effects=[],
        forbidden_effects=[
            "file_write",
            "network_request",
            "process_execution",
        ],
        required_effects=[],
        evidence_types=[
            "tool_return",
            "call_id_binding",
        ],
        observable_state=[],
        risk_level="low",
    ),
    "multiply": ToolCapability(
        name="multiply",
        argument_constraints={
            "a": {"gte": -1_000_000, "lte": 1_000_000},
            "b": {"gte": -1_000_000, "lte": 1_000_000},
        },
        required_permissions=["arithmetic:execute"],
        allowed_effects=[],
        forbidden_effects=[
            "file_write",
            "network_request",
            "process_execution",
        ],
        required_effects=[],
        evidence_types=[
            "tool_return",
            "call_id_binding",
        ],
        observable_state=[],
        risk_level="low",
    ),
    "divide": ToolCapability(
        name="divide",
        argument_constraints={
            "a": {"gte": -1_000_000, "lte": 1_000_000},
            "b": {
                "gte": -1_000_000,
                "lte": 1_000_000,
                "not_in": [0],
            },
        },
        required_permissions=["arithmetic:execute"],
        allowed_effects=[],
        forbidden_effects=[
            "file_write",
            "network_request",
            "process_execution",
        ],
        required_effects=[],
        evidence_types=[
            "tool_return",
            "call_id_binding",
        ],
        observable_state=[],
        risk_level="low",
    ),
}

TEXT_CAPABILITIES: dict[str, ToolCapability] = {
    name: ToolCapability(
        name=name,
        argument_constraints={
            field: {"required": True}
            for field in fields
        },
        required_permissions=["text:process"],
        allowed_effects=[],
        forbidden_effects=["file_write", "network_request", "process_execution"],
        evidence_types=["tool_return", "call_id_binding"],
        risk_level="low",
    )
    for name, fields in {
        "text_stats": ["text"],
        "find_text": ["text", "query"],
        "replace_text": ["text", "old", "new"],
    }.items()
}

DATA_CAPABILITIES: dict[str, ToolCapability] = {
    name: ToolCapability(
        name=name,
        argument_constraints={
            field: {"required": True}
            for field in fields
        },
        required_permissions=["data:process"],
        allowed_effects=[],
        forbidden_effects=["file_write", "network_request", "process_execution"],
        evidence_types=["tool_return", "call_id_binding"],
        risk_level="low",
    )
    for name, fields in {
        "parse_json": ["text"],
        "select_fields": ["record", "fields"],
        "filter_records": ["records", "field", "equals"],
    }.items()
}

TOOL_CAPABILITIES: dict[str, ToolCapability] = {
    **ARITHMETIC_CAPABILITIES,
    **TEXT_CAPABILITIES,
    **DATA_CAPABILITIES,
    "verify_authorized_record": ToolCapability(
        name="verify_authorized_record",
        argument_constraints={
            "name": {"required": True},
            "clearance": {"eq": "approved"},
        },
        required_permissions=["evidence:verify"],
        forbidden_effects=["file_write", "network_request", "process_execution"],
        evidence_types=[
            "tool_return",
            "call_id_binding",
            "authorization_receipt",
        ],
        risk_level="low",
    ),
    "read_external_content": ToolCapability(
        name="read_external_content",
        argument_constraints={"content": {"required": True}},
        required_permissions=["external:read"],
        forbidden_effects=["file_write", "network_request", "process_execution"],
        evidence_types=[
            "tool_return",
            "call_id_binding",
            "external_content_receipt",
        ],
        data_source=DataSource.EXTERNAL,
        trust_level=TrustLevel.LOW,
        risk_level="medium",
    ),
    "send_email": ToolCapability(
        name="send_email",
        argument_constraints={
            "recipient": {"required": True},
            "subject": {"required": True},
            "body": {"required": True},
        },
        required_permissions=["email:send"],
        allowed_effects=["sandbox_email_sent"],
        forbidden_effects=[
            "email_sent_to_unauthorized_recipient",
            "secret_disclosed",
            "attachment_added",
            "multiple_messages_sent",
        ],
        required_effects=["sandbox_email_sent"],
        evidence_types=[
            "tool_return",
            "call_id_binding",
            "sandbox_email_receipt",
            "message_id",
            "state_diff",
        ],
        observable_state=["sandbox_mailbox"],
        risk_level="high",
    ),
}


def get_tool_capability(tool_name: str) -> ToolCapability:
    try:
        return TOOL_CAPABILITIES[tool_name]
    except KeyError as exc:
        raise LookupError(
            f"No ToolCapability declared for tool: {tool_name}"
        ) from exc
