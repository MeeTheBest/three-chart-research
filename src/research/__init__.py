"""Research-output helpers with lazy exports for clean ``python -m`` execution."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .validator import ValidationIssue

__all__ = ["ValidationIssue", "validate_analysis"]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from .validator import ValidationIssue, validate_analysis

        return {"ValidationIssue": ValidationIssue, "validate_analysis": validate_analysis}[name]
    raise AttributeError(name)
