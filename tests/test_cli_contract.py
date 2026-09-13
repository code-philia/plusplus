from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from main import build_parser


REPO_ROOT = Path(__file__).resolve().parents[1]


class CliContractTests(unittest.TestCase):
    def test_compile_parameters_remain_available(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "compile",
                "requirements",
                "-o",
                "workspace/output",
                "--type",
                "web",
                "--port",
                "3301",
                "--resume",
                "--retry",
                "REQ-1.1",
            ]
        )

        self.assertEqual(args.requirement_path, "requirements")
        self.assertEqual(args.output_dir, "workspace/output")
        self.assertEqual(args.app_type, "web")
        self.assertEqual(args.port, 3301)
        self.assertTrue(args.resume)
        self.assertEqual(args.retry, ["REQ-1.1"])

    def test_config_and_doctor_commands_remain_registered(self) -> None:
        parser = build_parser()

        self.assertEqual(parser.parse_args(["config"]).command, "config")
        self.assertEqual(parser.parse_args(["doctor"]).command, "doctor")

    def test_compile_command_runs_frontend_without_agent_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "output"
            env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src"), "NO_COLOR": "1"}
            completed = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "src" / "main.py"),
                    "compile",
                    str(REPO_ROOT / "example" / "ticketbooking-demo"),
                    "-o",
                    str(output_dir),
                    "--type",
                    "web",
                ],
                cwd=str(REPO_ROOT),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
            )

            self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
            self.assertIn("Compilation Summary", completed.stdout)
            self.assertTrue((output_dir / ".arc" / "compiler" / "requirement_ir.json").is_file())
            self.assertTrue((output_dir / ".arc" / "traceability" / "requirements.json").is_file())


if __name__ == "__main__":
    unittest.main()
