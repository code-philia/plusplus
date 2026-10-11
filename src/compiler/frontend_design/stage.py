"""Postorder frontend passes with explicit direct ownership and reuse associations."""
from __future__ import annotations

from ..ir.backend import module_fields

import copy
import json
import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from utils.jsonio import write_json_atomic
from utils.logging import SynchronousLog

from model.contracts import StructuredModel
from model.client import describe_model_error
from compiler.preprocessing.traversal import children_by_id, postorder
from compiler.preprocessing.visual import ResolvedVisualReference
from .contracts import PHASE_TABLES, PROMPTS, patch_schema
from .context import TABLES, frontend_ir_traceability, local_context, ranked, required_types, visual_screen_matches, context_ui_nodes
from compiler.ir.frontend import frontend_events
from compiler.ir.frontend_storage import read_frontend_ir, write_frontend_ir


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
    def __init__(self, model: StructuredModel, artifact_root: Path) -> None:
        self.model = model
        self.root = artifact_root / "design" / "frontend"
        self.artifact_root = artifact_root
        self.log = SynchronousLog("FrontendDesignPass", workspace_root=artifact_root.resolve().parent)
        if hasattr(model, "set_usage_path"):
            model.set_usage_path(artifact_root / "model_usage.jsonl")

    def compile(self, requirement_ir: dict[str, Any], dependency_graph: dict[str, Any],
                backend_design_ir: dict[str, Any],
                visual_references: list[ResolvedVisualReference | dict[str, Any]],
                visual_analyses: list[dict[str, Any]] | None = None) -> FrontendIRGenerationResult:
        self.nodes = requirement_ir.get("nodes", {})
        self.children = children_by_id(requirement_ir)
        order = postorder(requirement_ir)
        self.dependencies = dependency_graph.get("requirement_dependencies", {})
        self.batches, self.failures, self.warnings = [], [], []
        self.apis = [copy.deepcopy(row) for row in backend_design_ir.get("modules", []) if row.get("kind") == "API"]
        self.backend_types = backend_design_ir.get("types", [])
        self.api_owners = {rid: [row["id"] for row in self.apis if rid in row["requirement_ids"]]
                           for rid in self.nodes}
        self.visuals = []
        cache_path = self.root / "visual_cache.json"
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.is_file() else {}
        except (OSError, ValueError):
            cached = {}
            self.warnings.append("Visual analysis cache could not be read; using supplied analyses.")
        if not isinstance(cached, dict):
            cached = {}
        analyses = {row.get("id", row.get("reference_id")): row for row in
                    [*cached.values(), *(visual_analyses or [])] if isinstance(row, dict)}
        for visual in visual_references:
            row = visual if isinstance(visual, dict) else visual.analysis_payload()
            key = row.get("id", row.get("reference_id"))
            self.visuals.append({"id": key, "path": row.get("path", row.get("source_path")),
                                 "requirement_ids": row.get("requirement_ids", []),
                                 "analysis": row.get("analysis") or analyses.get(key, {}).get("analysis", {})})
        self.ir = {"ir_kind": "frontend_design", "backend_ref": "../backend/backend_ir.json",
                   "types": [], "root_ui_id": "ui.app", "ui": [{
                       "id": "ui.app", "name": "application_shell", "kind": "group", "spec": "Application UI composition root.",
                       "requirement_ids": [], "children": [], "bindings": {}, "events": [],
                   }], "data": [], "functions": [], "pending_capabilities": [],
                   "requirement_links": {}}
        self.requirement_hash = hashlib.sha256(json.dumps(requirement_ir, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        existing = self.root / "frontend_ir.json"
        previous_report = self.root / "report.json"
        if existing.is_file():
            previous = read_frontend_ir(existing)
            report = json.loads(previous_report.read_text(encoding="utf-8")) if previous_report.is_file() else {}
            if report.get("requirement_sha256") not in (None, self.requirement_hash):
                raise ValueError("Existing frontend design belongs to changed requirements; use a new workspace")
            if previous.get("ir_kind") != "frontend_design":
                raise ValueError("Existing frontend design is not a semantic Frontend IR")
            if "events" in previous or any("events" not in ui for ui in previous["ui"]):
                raise ValueError("Frontend IR must embed events in UI nodes; regenerate the old design")
            self.ir = previous
        self.requested_ui: dict[str, set[str]] = {}
        report_path = self.artifact_root / "design" / "backend" / "report.json"
        backend_report = json.loads(report_path.read_text()) if report_path.is_file() else {}
        gaps = {row["id"]: row for row in self.ir.get("pending_capabilities", [])}
        for row in backend_report.get("pending_capabilities", []):
            gaps.setdefault(row["id"], copy.deepcopy(row))
        self.ir["pending_capabilities"] = list(gaps.values())
        if self.visuals:
            visuals = {row["id"]: row for row in self.ir.get("visual_references", [])}
            visuals.update({row["id"]: {"id": row["id"], "path": row["path"]} for row in self.visuals})
            self.ir["visual_references"] = list(visuals.values())
        # Design children before their parent; schedule every requirement once.
        for rid in order:
            phases = ("ui", "data", "interaction")
            for phase in phases:
                self._call(phase, [rid], self._payload(rid, phase))
        self.warnings.extend(f"{gap['id']}: pending integration: {gap['capability']} ({gap['reason']})"
                             for gap in self.ir["pending_capabilities"])
        status = "GENERATED_WITH_WARNINGS" if self.failures or self.warnings else "GENERATED"
        if not order:
            status = "FAILED"
            self.warnings.append("No requirements were supplied.")
        if self.failures:
            self.warnings.append(f"{len(self.failures)} generation calls failed; the IR contains partial design work.")
        links = frontend_ir_traceability(self.ir)
        traceability = {rid: links.get(rid, {"ui_scope": "NO_UI"}) for rid in order}
        report = {"status": status, "requirement_sha256": self.requirement_hash, "warnings": self.warnings, "failed_tasks": self.failures,
                  "pending_capabilities": copy.deepcopy(self.ir["pending_capabilities"]),
                  "requirement_links": copy.deepcopy(self.ir["requirement_links"]),
                  "traversal": "depth_first_postorder", "node_order": order,
                  "task_counts": {phase: sum(row["phase"] == phase for row in self.batches) for phase in PHASE_TABLES},
                  "requirement_entities": {rid: [key for name, values in traceability[rid].items()
                                                if name.endswith("_ids") and name != "api_ids" for key in values]
                                           for rid in order}}
        write_frontend_ir(self.root / "frontend_ir.json", self.ir)
        write_json_atomic(self.root / "requirements.json", {"requirements": traceability})
        write_json_atomic(self.root / "report.json", report)
        write_json_atomic(self.root / "pending_capabilities.json", {"pending_capabilities": self.ir["pending_capabilities"]})
        return FrontendIRGenerationResult(self.ir, report, self.batches, traceability)

    def _payload(self, rid: str, phase: str) -> dict[str, Any]:
        requirement = self._requirement(rid)
        related = self._related(rid)
        descendants, pending = set(), list(self.children[rid])
        while pending:
            key = pending.pop()
            if key in descendants:
                continue
            descendants.add(key)
            pending.extend(self.children[key])
        related = list(dict.fromkeys([*related, *sorted(descendants)]))
        matches = visual_screen_matches(self.ir, rid, self.nodes)
        context = local_context(self.ir, rid, requirement, set(related), self.requested_ui.get(rid, set()),
                                screen_matches=matches)
        owners = set(related) | {rid}
        attached = {key for owner in owners for key in self.api_owners.get(owner, [])}
        primary = [row for row in self.apis if row["id"] in attached
                   or owners & set(row.get("requirement_ids", []))]
        primary_ids = {row["id"] for row in primary}
        apis = primary + ranked([row for row in self.apis if row["id"] not in primary_ids], requirement, 8)
        related_rows, related_size = [], 0
        for row in ranked([self._requirement(key, compact=True) for key in related], requirement, 24):
            cost = len(json.dumps(row, ensure_ascii=False))
            if related_size + cost > 12000:
                continue
            related_rows.append(row)
            related_size += cost
        payload = {**context, "new_id_prefix": f"{rid}.", "requirement": requirement,
                   "node_role": "NON_LEAF" if self.children[rid] else "LEAF",
                   "child_requirements": ranked([self._requirement(key, compact=True) for key in self.children[rid]], requirement, 12),
                   "child_requirement_count": len(self.children[rid]),
                   "related_requirements": related_rows,
                   "api_contracts": [self._api(row) for row in apis] if phase != "ui" else [],
                   "api_catalog": [{"id": row["id"], "spec": row.get("spec", "")[:180]}
                                   for row in ranked(self.apis, requirement, 24)] if phase != "ui" else [],
                   "visual_references": [self._visual_context(row, phase) for row in self.visuals
                                         if owners & set(row["requirement_ids"])],
                   "pending_capabilities": [copy.deepcopy(row) for row in self.ir["pending_capabilities"]
                                            if owners & set(row["requirement_ids"])],
                   "prior_task_failures": [row for row in self.failures if owners & set(row["requirement_ids"])]}
        payload["types"] = required_types(payload, [*self.backend_types, *self.ir["types"]])
        return payload

    @staticmethod
    def _visual_context(row: dict[str, Any], phase: str) -> dict[str, Any]:
        if phase == "ui":
            return copy.deepcopy(row)
        keys = ("visible_controls",)
        analysis = row.get("analysis", {})
        return {"id": row["id"], "requirement_ids": row["requirement_ids"],
                "analysis": {key: copy.deepcopy(analysis[key]) for key in keys if key in analysis}}

    def _requirement(self, rid: str, compact: bool = False) -> dict[str, Any]:
        row = self.nodes[rid]
        result = {"id": rid, **{key: copy.deepcopy(row[key]) for key in
                  ("name", "type", "parent_id", "description", "scenarios", "visual_reference_ids", "visual_references") if key in row}}
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
        ids.extend(self.children[rid])
        # Shared objects bring the requirements whose behavior must be preserved.
        context = local_context(self.ir, rid, self._requirement(rid), set(ids), self.requested_ui.get(rid, set()),
                                screen_matches=visual_screen_matches(self.ir, rid, self.nodes))
        ids.extend(owner for row in [*context_ui_nodes(context), *context["existing"]["data"],
                                    *context["existing"]["functions"]]
                   for owner in row.get("requirement_ids", []))
        ids.extend(owner for row in context_ui_nodes(context) for event in row.get("events", [])
                   for owner in event.get("requirement_ids", []))
        return [key for key in dict.fromkeys(ids) if key in self.nodes and key != rid]

    @staticmethod
    def _api(row: dict[str, Any]) -> dict[str, Any]:
        def fields(key: str) -> list[dict[str, Any]]:
            return [{**copy.deepcopy(field), "type": copy.deepcopy(field.get("backend_type", field["type"]))}
                    for field in module_fields(row, key)]
        return {"id": row["id"], "spec": row.get("spec", ""), "inputs": fields("inputs"),
                "outputs": fields("outputs"),
                **{key: row[key] for key in ("path", "method", "authentication", "status_code", "content_type")}}

    def _merge(self, phase: str, output: dict[str, Any], requirements: list[str]) -> None:
        """Merge a region atomically while preserving tree and independent event ownership."""
        trial = copy.deepcopy(self.ir)
        direct_updates = set(output.get('implements_ids', []))
        for table in PHASE_TABLES[phase]:
            registry = {row["id"]: row for row in trial[table]}
            for key in output.get("deletes", {}).get(table, []):
                registry.pop(key, None)
            for source in output.get("upserts", {}).get(table, []):
                row = copy.deepcopy(source)
                if table != "types":
                    previous = registry.get(row["id"])
                    supplied = row.get("requirement_ids", [])
                    if previous is None:
                        owners = requirements if supplied else []
                    else:
                        owners = previous.get("requirement_ids", [])
                        # Structural maintenance never establishes direct ownership.
                        semantic_keys = (set(row) | set(previous)) - {"name", "requirement_ids", "children", "events", "presentation", "visual_reference_ids"}
                        semantic_change = any(row.get(key) != previous.get(key) for key in semantic_keys)
                        if semantic_change and row['id'] in direct_updates:
                            owners = [*owners, *requirements]
                    row["requirement_ids"] = list(dict.fromkeys(owners))
                if table == "pending_capabilities":
                    row["function_ids"] = list(dict.fromkeys([
                        *registry.get(row["id"], {}).get("function_ids", []), *row["function_ids"]]))
                if table == "ui":
                    old_events = {event["id"]: event for event in registry.get(row["id"], {}).get("events", [])}
                    for event in row.get("events", []):
                        old = old_events.get(event["id"])
                        if old is not None:
                            event["requirement_ids"] = list(dict.fromkeys([
                                *old.get("requirement_ids", []),
                                *(requirements if event["id"] in direct_updates else [])]))
                        else:
                            event["requirement_ids"] = list(requirements) if event.get("requirement_ids") else []
                    supplied_ids = {event["id"] for event in row.get("events", [])}
                    if phase != "interaction":
                        row["events"] = copy.deepcopy(list(old_events.values()))
                    else:
                        row.setdefault("events", []).extend(copy.deepcopy(event) for key, event in old_events.items()
                            if key not in supplied_ids and set(event.get("requirement_ids", [])) - set(requirements))
                registry[row["id"]] = row
            trial[table] = list(registry.values())
        records = {row["id"]: row for table in TABLES if table != "types" for row in trial[table]}
        records.update({event["id"]: event for event in frontend_events(trial)})
        touched = set(output.get("reuse_ids", [])) | {
            row["id"] for table in PHASE_TABLES[phase] if table not in {"types", "pending_capabilities"}
            for row in output.get("upserts", {}).get(table, [])}
        touched.update(output.get("changed_event_ids", []))
        links = trial.setdefault("requirement_links", {})
        for rid in requirements:
            direct = {key for key, row in records.items() if rid in row.get("requirement_ids", [])}
            prior = set(links.get(rid, {}).get("uses", []))
            links[rid] = {"implements": sorted(direct),
                          "uses": sorted(((prior | touched) & records.keys()) - direct)}
        for rid, link in links.items():
            link["implements"] = sorted(key for key, row in records.items() if rid in row.get("requirement_ids", []))
            link["uses"] = sorted((set(link["uses"]) & records.keys()) - set(link["implements"]))
        write_frontend_ir(self.root / "frontend_ir.json", trial)
        self.ir = trial

    def _call(self, phase: str, requirements: list[str], payload: dict[str, Any]) -> None:
        task_id = f"{phase}-{len(self.batches) + 1:05d}"
        schema = patch_schema(phase)
        record = {"task_id": task_id, "phase": phase, "requirement_ids": requirements,
                  "input": payload, "instructions": PROMPTS[phase], "output_schema": schema}
        started = time.monotonic()
        record["context_requests"] = []
        self.log.info(f"MODEL_REQUEST phase=frontend_{phase} task={task_id} requirements={requirements}")
        try:
            kwargs = {"schema_name": f"frontend_{phase}", "instructions": PROMPTS[phase],
                      "input_payload": payload, "output_schema": schema}
            output = self.model.generate_json(**kwargs)
            for _ in range(4):
                from compiler.backend_design.validation import shape_errors
                errors = shape_errors(output, schema)
                if errors:
                    raise ValueError("Invalid frontend delta: " + "; ".join(errors))
                requested = output.get("load_ui_ids", [])
                if not requested:
                    break
                if any(output["upserts"].values()) or any(output["deletes"].values()) or output["ui_updates"] or output["implements_ids"] or output["reuse_ids"]:
                    raise ValueError("UI context requests must contain an otherwise empty delta")
                known = {row["id"] for row in self.ir["ui"]}
                if set(requested) - known:
                    raise ValueError("Context request contains unknown UI IDs")
                record["context_requests"].append({"ids": requested, "output": output})
                self.requested_ui[requirements[0]] = set(requested)
                payload = self._payload(requirements[0], phase)
                kwargs["input_payload"] = payload
                output = self.model.generate_json(**kwargs)
            if output.get("load_ui_ids"):
                raise ValueError("UI context request limit exceeded; design a smaller region")
            loaded = set(payload["loaded_ui_ids"])
            known = {row["id"] for row in self.ir["ui"]}
            changed = {row["id"] for row in output.get("upserts", {}).get("ui", [])}
            changed.update(output.get("deletes", {}).get("ui", []))
            changed.update(row["id"] for row in output.get("ui_updates", []))
            if (changed & known) - loaded:
                raise ValueError("Cannot edit unloaded existing UI; request load_ui_ids first")
            record["output"] = output
            record["final_input"] = payload
            from .delta import materialize_ui_updates
            output = materialize_ui_updates(self.ir, phase, output, loaded, requirements)
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
