"""Deterministic mapping from recovery decisions to graph routes."""

from typing import Literal

from recap.schemas import RecoveryAction

RecoveryRoute = Literal["replan", "observe_think", "human_approval", "end"]

_RECOVERY_ROUTES: dict[RecoveryAction, RecoveryRoute] = {
    RecoveryAction.BLOCK: "end",
    RecoveryAction.REPLAN: "replan",
    RecoveryAction.PARAMETER_FIX: "replan",
    RecoveryAction.PURIFY: "observe_think",
    RecoveryAction.KEEP_UNFINISHED: "replan",
    RecoveryAction.HUMAN_ESCALATION: "human_approval",
}


def route_for_recovery(action: RecoveryAction) -> RecoveryRoute:
    return _RECOVERY_ROUTES[action]
