from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from arcbench_agent_runtime.runtime import AgentRuntime
from compiler import CompilationRequest, Compiler
from compiler.frontend import RequirementFrontend


REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_REQUIREMENTS = REPO_ROOT / "example" / "ticketbooking-demo" / "requirements.yaml"


class RequirementFrontendTests(unittest.TestCase):
    def test_frontend_output_is_deterministic_for_same_source(self) -> None:
        frontend = RequirementFrontend()

        first = frontend.compile(DEMO_REQUIREMENTS)
        second = frontend.compile(DEMO_REQUIREMENTS)

        self.assertEqual(first.requirement_ir, second.requirement_ir)
        self.assertEqual(first.dependency_graph, second.dependency_graph)
        self.assertEqual(
            [item.to_dict() for item in first.diagnostics],
            [item.to_dict() for item in second.diagnostics],
        )

    def test_compiles_demo_into_atomic_dependency_waves(self) -> None:
        result = RequirementFrontend().compile(DEMO_REQUIREMENTS)

        self.assertTrue(result.ok, [item.to_dict() for item in result.diagnostics])
        self.assertEqual(
            result.requirement_ir["atomic_units"],
            ["REQ-1.1", "REQ-1.2", "REQ-2.1", "REQ-2.2", "REQ-3.1", "REQ-3.2"],
        )
        self.assertEqual(
            result.dependency_graph["implementation_waves"],
            [
                ["REQ-1.1", "REQ-2.1"],
                ["REQ-1.2", "REQ-2.2"],
                ["REQ-3.1"],
                ["REQ-3.2"],
            ],
        )
        self.assertEqual(
            result.requirement_ir["nodes"]["REQ-1.1"]["scenarios"][0]["id"],
            "REQ-1.1:scenario:1",
        )
        self.assertIn(
            "./reference/register.png",
            result.requirement_ir["nodes"]["REQ-1.1"]["visual_references"],
        )
        self.assertEqual(result.requirement_ir["nodes"]["REQ-1.1"]["type"], "ATOMIC")
        self.assertEqual(
            result.requirement_ir["nodes"]["REQ-1.1"]["source"]["pointer"],
            "/children/0/children/0",
        )

    def test_reports_unresolved_dependency_and_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            requirement_path = Path(directory) / "requirements.yaml"
            requirement_path.write_text(
                """id: ROOT
type: FOLDER
children:
  - id: REQ-A
    type: ATOMIC
    dependencies: [REQ-B, REQ-MISSING]
  - id: REQ-B
    type: ATOMIC
    dependencies: [REQ-A]
""",
                encoding="utf-8",
            )

            result = RequirementFrontend().compile(requirement_path)

        self.assertFalse(result.ok)
        self.assertEqual(
            {item.code for item in result.diagnostics},
            {"ARC1302", "ARC1303"},
        )


class CompilerIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_persists_frontend_artifacts_and_traceability(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            runtime = AgentRuntime.from_env(project_dir=str(output_dir))
            runtime.traceability.init_store()
            compiler = Compiler(runtime, lambda *_args: None)

            result = await compiler.compile(
                CompilationRequest(
                    requirement_path=DEMO_REQUIREMENTS,
                    output_dir=output_dir,
                )
            )

            self.assertTrue(result.ok)
            self.assertFalse(result.complete)
            requirement_ir = json.loads((output_dir / ".arc" / "compiler" / "requirement_ir.json").read_text(encoding="utf-8"))
            queue = json.loads((output_dir / ".arc" / "processing_queue.json").read_text(encoding="utf-8"))
            scenarios = runtime.traceability.list_scenarios()
            traced_requirement = runtime.traceability.get_requirement("REQ-1.1")

        self.assertEqual(requirement_ir["root_id"], "ROOT")
        self.assertEqual(queue["schema_version"], 2)
        self.assertEqual(queue["passes"][0]["status"], "COMPLETED")
        self.assertEqual(len(scenarios), 12)
        self.assertEqual(traced_requirement["type"], "ATOMIC")
        self.assertEqual(traced_requirement["source"]["pointer"], "/children/0/children/0")


if __name__ == "__main__":
    unittest.main()
