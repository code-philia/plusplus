from __future__ import annotations

from dataclasses import dataclass, field

from ..ir.design.module import DesignModule
from .contract_checker import check_contracts
from .coverage_checker import check_coverage
from .database_checker import check_database
from .frontend_checker import check_frontend
from .reference_checker import check_references


@dataclass(slots=True)
class VerificationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, object]:
        return {"ok": self.ok, "errors": list(self.errors), "warnings": list(self.warnings), "checks": dict(self.checks)}


class DesignIRVerifier:
    checks = (
        ("references", check_references, False),
        ("contracts", check_contracts, False),
        ("database", check_database, False),
        ("frontend", check_frontend, False),
        ("coverage", check_coverage, True),
    )

    def verify(self, graph: DesignModule) -> VerificationReport:
        report = VerificationReport()
        for name, checker, advisory in self.checks:
            issues = checker(graph)
            report.checks[name] = "PASSED" if not issues else "WARNINGS" if advisory else "FAILED"
            if advisory:
                report.warnings.extend(issues)
            else:
                report.errors.extend(issues)
        return report


__all__ = ["DesignIRVerifier", "VerificationReport"]
