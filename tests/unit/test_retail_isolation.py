import importlib.util
import sys
from pathlib import Path

import pytest
from recap.tools import ToolRegistry, execute_trusted_tool


def _load_retail_module():
    path = Path(__file__).resolve().parents[1] / "tools.py"
    name = "recap_retail_evaluation_tools"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_environments_have_independent_database_tools_and_observers(tmp_path) -> None:
    retail = _load_retail_module()
    first = retail.build_retail_environment(tmp_path / "case-1")
    second = retail.build_retail_environment(tmp_path / "case-2")

    first.database.inventory["SKU-100"] = 0
    first.database.identity_sessions["only-first"] = {"verified": True}

    assert second.database.inventory["SKU-100"] == 42
    assert "only-first" not in second.database.identity_sessions
    assert first.database.path != second.database.path
    assert first.tools["lookup_order"] is not second.tools["lookup_order"]
    assert first.observers["verify_identity"] is not second.observers["verify_identity"]


def test_seed_clears_all_case_state_before_reinitializing(tmp_path) -> None:
    retail = _load_retail_module()
    environment = retail.build_retail_environment(tmp_path / "case")
    db = environment.database
    db.identity_sessions["stale"] = {"verified": True}
    db.refund_tickets.append({"ticket_id": "stale"})
    db.inventory["stale"] = 99

    db.seed()

    assert db.identity_sessions == {}
    assert db.refund_tickets == []
    assert "stale" not in db.inventory
    assert db.inventory["SKU-100"] == 42


@pytest.mark.asyncio
async def test_refund_persists_only_inside_own_case_workspace(tmp_path) -> None:
    retail = _load_retail_module()
    first = retail.build_retail_environment(tmp_path / "case-1")
    second = retail.build_retail_environment(tmp_path / "case-2")
    first.database.identity_sessions["session-1"] = {
        "customer_id": "C001",
        "order_id": "O001",
        "verified": True,
    }

    result = await first.tools["submit_refund_request"].ainvoke(
        {"order_id": "O001", "session_token": "session-1"}
    )

    assert result.status.value == "success"
    assert len(first.database.refund_tickets) == 1
    assert second.database.refund_tickets == []
    assert first.database.path.exists()


@pytest.mark.asyncio
async def test_identity_observer_issues_wrapper_auditable_authorization_fact(tmp_path) -> None:
    retail = _load_retail_module()
    environment = retail.build_retail_environment(tmp_path / "identity-case")
    registry = ToolRegistry()
    registry.register(
        environment.tools["verify_identity"],
        environment.scenario.capabilities["verify_identity"],
        environment.observers["verify_identity"],
    )

    result = await execute_trusted_tool(
        registry=registry,
        tool_name="verify_identity",
        args={
            "phone": "555-0101",
            "email": "alice@example.com",
            "order_id": "O001",
        },
        model_tool_call_id="identity-model-call",
        timeout_seconds=1,
    )

    assert result.success is True
    assert len(result.authorization_facts) == 1
    fact = result.authorization_facts[0]
    assert fact.fact_type == "verified_customer_session"
    assert fact.issuer_tool == "verify_identity"
    assert fact.claims["session_token"] == result.content["session_token"]
    assert fact.claims["order_id"] == "O001"
