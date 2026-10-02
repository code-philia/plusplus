from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FrontendPassSpec:
    name: str
    phase: str
    input_level: str = "requirement"
    output_level: str = "design"


class FrontendDesignPass:
    spec: FrontendPassSpec

    def run(self, compiler, tasks):
        if self.spec.phase == "data":
            return compiler._run_frontend_data_phase(tasks)
        return compiler._run_frontend_phase(self.spec.phase, tasks)


__all__ = ["FrontendDesignPass", "FrontendPassSpec"]
