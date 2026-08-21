"""Pure JSON and record-processing tools."""

import json
from typing import Any

from langchain_core.tools import tool


@tool
def parse_json(text: str) -> Any:
    """Parse a JSON string into structured data."""
    return json.loads(text)


@tool
def select_fields(record: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    """Select named fields from one record, omitting fields that are absent."""
    return {field: record[field] for field in fields if field in record}


@tool
def filter_records(
    records: list[dict[str, Any]],
    field: str,
    equals: Any,
) -> list[dict[str, Any]]:
    """Keep records whose selected field equals the supplied value."""
    return [record for record in records if record.get(field) == equals]


DATA_TOOLS = [parse_json, select_fields, filter_records]
