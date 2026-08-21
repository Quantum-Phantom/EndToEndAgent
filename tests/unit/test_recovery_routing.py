import pytest

from recap.recovery import route_for_recovery
from recap.schemas import RecoveryAction


@pytest.mark.parametrize(
    ("action", "route"),
    [
        (RecoveryAction.BLOCK, "end"),
        (RecoveryAction.REPLAN, "replan"),
        (RecoveryAction.PARAMETER_FIX, "replan"),
        (RecoveryAction.PURIFY, "observe_think"),
        (RecoveryAction.KEEP_UNFINISHED, "replan"),
        (RecoveryAction.HUMAN_ESCALATION, "human_approval"),
    ],
)
def test_recovery_action_has_a_deterministic_graph_route(action, route) -> None:
    assert route_for_recovery(action) == route
