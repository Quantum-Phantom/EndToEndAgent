"""Protocol for trusted, scenario-provided environment observers."""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class EffectObserver(Protocol):
    """Collect trusted effects outside the tool implementation.

    Tools return business data only. The wrapper calls ``snapshot`` before and
    after invocation, then asks this observer—not the tool—to derive the state
    diff, semantic effects, and effect evidence.
    """

    def snapshot(self) -> dict[str, Any] | None: ...

    def effects(
        self,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        tool_return: Any,
    ) -> tuple[list[str], list[str], dict[str, Any] | None]: ...
