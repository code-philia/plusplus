"""Four generation passes over the lightweight frontend IR; no review gates."""
from __future__ import annotations

import base64
import copy
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from arcbench_agent_runtime.jsonio import write_json_atomic
from core.logging import SynchronousLog

from ..model_client import StructuredModel, describe_model_error
from ..visual_reference import ResolvedVisualReference, VisualModel, VisualStructuredModel
from .contracts import PHASE_TABLES, PROMPTS, patch_schema
from .context import TABLES, frontend_ir_traceability, local_context, preorder, ranked, required_types


@dataclass
class FrontendIRGenerationResult:
    frontend_ir: dict[str, Any]
    report: dict[str, Any]
    batches: list[dict[str, Any]] = field(default_factory=list)
    traceability: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.report["status"] in {"GENERATED", "GENERATED_WITH_WARNINGS"}


class FrontendIRGenerationPass:
    def __init__(self, model: StructuredModel, artifact_root: Path,
                 visual_model: VisualStructuredModel | None = None) -> None:
        self.model, self.visual_model = model, visual_model
        self.root = artifact_root / "design" / "frontend"
        self.artifact_root = artifact_root
        self.log = SynchronousLog("FrontendDesignPass", workspace_root=artifact_root.resolve().parent)
        if hasattr(model, "set_usage_path"):
            model.set_usage_path(artifact_root / "model_usage.jsonl")

    def compile(self, requirement_ir: dict[str, Any], dependency_graph: dict[str, Any],
                backend_design_ir: dict[str, Any],
                visual_references: list[ResolvedVisualReference | dict[str, Any]],
                visual_analyses: list[dict[str, Any]] | None = None) -> FrontendIRGenerationResult:
        backend_design_ir = backend_design_ir.get("backend_blueprint", backend_design_ir)
        self.nodes = requirement_ir.get("nodes", {})
        order = preorder(requirement_ir)
        self.dependencies = dependency_graph.get("requirement_dependencies", {})
        self.batches, self.failures, self.warnings = [], [], []
        self.apis = [copy.deepcopy(row) for row in backend_design_ir.get("modules", []) if row.get("kind") == "API"]
        self.backend_types = backend_design_ir.get("types", [])
        self.api_owners = {row.get("requirement_id", row.get("id")): row.get("api_ids", [])
                           for row in backend_design_ir.get("requirements", [])}
        self.visuals = []
        self.visual_objects = {}
        analyses = {row.get("id", row.get("reference_id")): row for row in visual_analyses or []}
        for visual in visual_references:
            row = visual if isinstance(visual, dict) else visual.analysis_payload()
            key = row.get("id", row.get("reference_id"))
            self.visual_objects[key] = visual
            self.visuals.append({"id": key, "path": row.get("path", row.get("source_path")),
                                 "requirement_ids": row.get("requirement_ids", []),
                                 "analysis": row.get("analysis", analyses.get(key, {}).get("analysis", {}))})
        self.ir = {"ir_kind": "frontend_design", "backend_ref": "../backend/backend_ir.json",
                   "types": [], "root_ui_id": "ui.app", "ui": [{
                       "id": "ui.app", "kind": "group", "spec": "Application UI composition root.",
                       "requirement_ids": [], "children": [], "bindings": {},
                   }], "data": [], "events": [], "functions": []}
        if self.visuals:
            self.ir["visual_references"] = [{"id": row["id"], "path": row["path"]} for row in self.visuals]
        # Bound system planning by top-level requirement branches. Each chunk sees
        # the accumulated topology, so shared screens are progressively reusable.
        branch_for: dict[str, str] = {}
        groups: dict[str, list[str]] = {}
        root_id = requirement_ir.get("root_id")
        for rid in order:
            parent = self.nodes[rid].get("parent_id")
            branch_for[rid] = branch_for.get(parent, rid) if parent != root_id else rid
            groups.setdefault(branch_for[rid], []).append(rid)
        for branch, ids in groups.items():
            for offset in range(0, len(ids), 24):
                selected = ids[offset:offset + 24]
                self._call("topology", selected, {
                    "new_id_prefix": f"{branch}.topology{offset // 24}.",
                    "requirement_catalog": [self._requirement(rid, compact=True) for rid in selected],
                    "root_ui_id": self.ir["root_ui_id"], "existing": {"ui": copy.deepcopy(self.ir["ui"])},
                    "visual_references": [row for row in self.visuals if set(selected) & set(row["requirement_ids"])],
                })
        for phase in ("ui", "data", "interaction"):
            for rid in order:
                requirement = self._requirement(rid)
                related = self._related(rid)
                context = local_context(self.ir, rid, requirement, set(related))
                owners = set(related) | {rid}
                attached = {key for owner in owners for key in self.api_owners.get(owner, [])}
                primary = [row for row in self.apis if row["id"] in attached
                           or owners & set(row.get("requirement_ids", []))]
                # Related APIs are never truncated; other APIs are ranked candidates.
                primary_ids = {row["id"] for row in primary}
                apis = primary + ranked([row for row in self.apis if row["id"] not in primary_ids], requirement, 8)
                payload = {**context, "new_id_prefix": f"{rid}.", "requirement": requirement,
                           "related_requirements": [self._requirement(key) for key in related],
                           "api_contracts": [self._api(row) for row in apis] if phase != "ui" else [],
                           "api_catalog": [{"id": row["id"], "spec": row.get("spec", "")[:180]}
                                           for row in ranked(self.apis, requirement, 24)] if phase != "ui" else [],
                           "visual_references": [row for row in self.visuals
                                                 if owners & set(row["requirement_ids"])],
                           "prior_task_failures": [row for row in self.failures if rid in row["requirement_ids"]]}
                payload["types"] = required_types(payload, [*self.backend_types, *self.ir["types"]])
                self._call(phase, [rid], payload)
        status = "GENERATED_WITH_WARNINGS" if self.failures or self.warnings else "GENERATED"
        if not order:
            status = "FAILED"
            self.warnings.append("No requirements were supplied.")
        if self.failures:
            self.warnings.append(f"{len(self.failures)} generation calls failed; the IR contains partial design work.")
        links = frontend_ir_traceability(self.ir)
        traceability = {rid: links.get(rid, {"ui_scope": "NO_UI"}) for rid in order}
        report = {"status": status, "warnings": self.warnings, "failed_tasks": self.failures,
                  "task_counts": {phase: sum(row["phase"] == phase for row in self.batches) for phase in PHASE_TABLES},
                  "requirement_entities": {rid: [key for name, values in traceability[rid].items()
                                                if name.endswith("_ids") and name != "api_ids" for key in values]
                                           for rid in order}}
        write_json_atomic(self.root / "frontend_ir.json", self.ir)
        write_json_atomic(self.root / "requirements.json", {"requirements": traceability})
        write_json_atomic(self.root / "report.json", report)
        return FrontendIRGenerationResult(self.ir, report, self.batches, traceability)

    def _requirement(self, rid: str, compact: bool = False) -> dict[str, Any]:
        row = self.nodes[rid]
        result = {"id": rid, **{key: copy.deepcopy(row[key]) for key in
                  ("name", "type", "parent_id", "description", "scenarios", "visual_reference_ids") if key in row}}
        if compact:
            result["description"] = str(result.get("description", ""))[:900]
            result.pop("scenarios", None)
        return result

    def _related(self, rid: str) -> list[str]:
        ids, parent, seen = [], self.nodes[rid].get("parent_id"), set()
        while parent in self.nodes and parent not in seen:
            seen.add(parent)
            ids.append(parent)
            parent = self.nodes[parent].get("parent_id")
        ids.extend(self.dependencies.get(rid, []))
        ids.extend(key for key, row in self.nodes.items() if row.get("parent_id") == rid)
        # Shared objects bring the requirements whose behavior must be preserved.
        context = local_context(self.ir, rid, self._requirement(rid), set(ids))
        ids.extend(owner for table, rows in context["existing"].items() for row in rows
                   if table != "ui" or (row.get("kind") not in {"screen", "group"}
                                         and rid in row.get("requirement_ids", []))
                   for owner in row.get("requirement_ids", []))
        return [key for key in dict.fromkeys(ids) if key in self.nodes and key != rid]

    @staticmethod
    def _api(row: dict[str, Any]) -> dict[str, Any]:
        def fields(key: str) -> list[dict[str, Any]]:
            return [{**copy.deepcopy(field), "type": copy.deepcopy(field.get("backend_type", field["type"]))}
                    for field in row.get(key, [])]
        return {"id": row["id"], "spec": row.get("spec", ""), "inputs": fields("inputs"),
                "outputs": fields("outputs"), "entrypoint": row.get("entrypoint", {})}

    def _merge(self, phase: str, output: dict[str, Any], requirements: list[str]) -> None:
        """Mechanical atomic merge only: no shape/type/reference/coverage inspection."""
        trial = copy.deepcopy(self.ir)
        for table in PHASE_TABLES[phase]:
            registry = {row["id"]: row for row in trial[table]}
            for key in output.get("deletes", {}).get(table, []):
                registry.pop(key, None)
            for source in output.get("upserts", {}).get(table, []):
                row = copy.deepcopy(source)
                if table != "types":
                    row["requirement_ids"] = list(dict.fromkeys([
                        *registry.get(row["id"], {}).get("requirement_ids", []),
                        *row.get("requirement_ids", []),
                        *(requirements if phase != "topology" else []),
                    ]))
                registry[row["id"]] = row
            trial[table] = list(registry.values())
        reused = set(output.get("reuse_ids", []))
        for table in TABLES:
            if table != "types":
                for row in trial[table]:
                    if row["id"] in reused:
                        row["requirement_ids"] = list(dict.fromkeys([*row.get("requirement_ids", []), *requirements]))
        write_json_atomic(self.root / "frontend_ir.json", trial)
        self.ir = trial

    def _call(self, phase: str, requirements: list[str], payload: dict[str, Any]) -> None:
        task_id = f"{phase}-{len(self.batches) + 1:05d}"
        schema = patch_schema(phase)
        record = {"task_id": task_id, "phase": phase, "requirement_ids": requirements,
                  "input": payload, "instructions": PROMPTS[phase], "output_schema": schema}
        started = time.monotonic()
        self.log.info(f"MODEL_REQUEST phase=frontend_{phase} task={task_id} requirements={requirements}")
        try:
            kwargs = {"schema_name": f"frontend_{phase}", "instructions": PROMPTS[phase],
                      "input_payload": payload, "output_schema": schema}
            image = None
            if phase == "ui":
                for row in payload.get("visual_references", []):
                    visual = self.visual_objects.get(row["id"])
                    if isinstance(visual, ResolvedVisualReference):
                        try:
                            image = f"data:{visual.media_type};base64," + base64.b64encode(visual.absolute_path.read_bytes()).decode("ascii")
                            break
                        except OSError as exc:
                            self.warnings.append(f"{row['id']}: image unavailable: {exc}; using analysis.")
            if image:
                if self.visual_model is None:
                    self.visual_model = VisualModel.from_env()
                    if hasattr(self.visual_model, "set_usage_path"):
                        self.visual_model.set_usage_path(self.artifact_root / "model_usage.jsonl")
                try:
                    output = self.visual_model.generate_visual_json(**kwargs, image_data_url=image)
                except Exception as exc:
                    self.warnings.append(f"{task_id}: visual generation failed: {describe_model_error(exc)}; using image analysis.")
                    output = self.model.generate_json(**kwargs)
            else:
                output = self.model.generate_json(**kwargs)
            record["output"] = output
            self._merge(phase, output, requirements)
            record["status"] = "APPLIED"
            self.log.info(f"MODEL_APPLIED phase=frontend_{phase} task={task_id}")
        except Exception as exc:
            message = describe_model_error(exc)
            record.update(status="FAILED", error=message)
            self.failures.append({"task_id": task_id, "phase": phase,
                                  "requirement_ids": requirements, "message": message})
            self.log.info(f"MODEL_FAILED phase=frontend_{phase} task={task_id} error={message}")
        record["duration_seconds"] = round(time.monotonic() - started, 3)
        self.batches.append(record)
        write_json_atomic(self.root / "passes" / f"{task_id}.json", record)
