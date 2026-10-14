"""Stage boundaries and scheduling without domain design or implementation policy."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from .preprocessing.traversal import postorder
from utils.jsonio import write_json_atomic

STAGES = ('project_initialization', 'visual_analysis', 'database_design', 'database_lowering',
          'backend_design', 'backend_lowering', 'frontend_design', 'frontend_lowering',
          'code_binding', 'implementation', 'build', 'validation_preparation')
ARTIFACT_DIRECTORIES = {
    'visual_analysis': 'design/frontend', 'database_design': 'design/database',
    'project_initialization': 'project', 'database_lowering': 'lowering/database',
    'backend_design': 'design/backend', 'backend_lowering': 'lowering/backend',
    'frontend_design': 'design/frontend', 'frontend_lowering': 'lowering/frontend',
    'code_binding': 'code_binding', 'implementation': 'implementation', 'build': 'build',
    'validation_preparation': 'validation_preparation',
}

@dataclass
class WorkflowContext:
    output_dir: Path
    requirements: dict[str, Any]
    dependencies: dict[str, Any]


def plan_stage(context: WorkflowContext, stage: str) -> tuple[dict[str, Any], Path]:
    if stage not in STAGES:
        raise ValueError(f'Unknown workflow stage: {stage}')
    order = postorder(context.requirements)
    passes = []
    if stage == 'database_design':
        passes = [{'pass': index, 'status': 'NOT_IMPLEMENTED'} for index in range(1, 4)]
    elif stage == 'backend_design':
        passes = [{'requirement_id': rid, 'pass': index, 'status': 'NOT_IMPLEMENTED'}
                  for rid in order for index in range(1, 4)]
    elif stage == 'implementation':
        passes = [{'requirement_id': rid, 'step': step, 'status': 'NOT_IMPLEMENTED'}
                  for rid in order for step in ('test_generation', 'test_execution',
                                               'backend_implementation', 'frontend_implementation',
                                               'test_execution_after_implementation', 'repair', 'verification')]
    report = {'stage': stage, 'status': 'NOT_IMPLEMENTED', 'passes': passes,
              'requirement_order': order, 'ir': None}
    if stage == 'frontend_design':
        report['pass_layout'] = 'UNDECIDED'
    path = context.output_dir / '.arc' / ARTIFACT_DIRECTORIES[stage] / 'workflow-shell.json'
    write_json_atomic(path, report)
    return report, path
