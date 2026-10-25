"""Validation primitives shared by all stages (spec Section 5.0 and 8.1).

A stage passes when no ``blocker`` or ``major`` check failed. Failed ``minor``
checks are recorded and surfaced as warnings.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

Severity = Literal["blocker", "major", "minor"]
_FAILING: frozenset[str] = frozenset({"blocker", "major"})


class Check(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    passed: bool
    severity: Severity
    detail: str = ""


class ValidationReport(BaseModel):
    stage: str
    checks: list[Check] = Field(default_factory=list)
    scores: dict[str, Any] | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def passed(self) -> bool:
        return not self.unresolved

    @property
    def unresolved(self) -> list[Check]:
        """Failed blockers and majors: they stop publication."""
        return [c for c in self.checks if not c.passed and c.severity in _FAILING]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if not c.passed and c.severity == "minor"]

    def feedback_items(self) -> list[dict[str, str]]:
        """Failed checks in the shape that is appended to a retry prompt."""
        return [
            {"name": c.name, "severity": c.severity, "detail": c.detail}
            for c in self.checks
            if not c.passed
        ]

    def issues(self) -> list[dict[str, Any]]:
        """Unresolved items in the API ``issues`` shape (Section 3, Get response)."""
        return [
            {
                "code": c.name.upper(),
                "severity": c.severity,
                "stage": self.stage,
                "scene_id": None,
                "message": c.detail,
            }
            for c in self.unresolved
        ]
