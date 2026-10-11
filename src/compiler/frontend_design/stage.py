"""Postorder frontend passes with explicit direct ownership and reuse associations."""
from __future__ import annotations

from ..ir.backend import module_fields

import base64
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
from model.vision import VisualModel
from model.contracts import VisualStructuredModel
from .contracts import PHASE_TABLES, PROMPTS, patch_schema, DISCOVERY_PROMPT, discovery_schema
from .context import TABLES, frontend_ir_traceability, local_context, ranked, required_types, ui_summary, visual_screen_matches
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
            self._discover(rid)
            phases = ("topology", "ui", "data", "interaction")
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

    def _discover(self, rid: str) -> None:
        if len(self.ir["ui"]) <= 64 and sum(len(json.dumps(row, ensure_ascii=False)) for row in self.ir["ui"]) <= 24000:
            return
        ui = {row["id"]: row for row in self.ir["ui"]}
        parents = {child: row["id"] for row in ui.values() for child in row.get("children", [])}
        matches = visual_screen_matches(self.ir, rid, self.nodes)
        screens = list(dict.fromkeys(row["screen_id"] for row in matches))
        pending, visited = [*screens, self.ir["root_ui_id"]], set()
        selected = self.requested_ui.setdefault(rid, set())
        for key in screens:
            if sum(len(json.dumps(ui[item], ensure_ascii=False)) for item in selected | {key}) <= 24000:
                selected.add(key)
        while pending:
            owner = pending.pop(0)
            if owner in visited or owner not in ui:
                continue
            visited.add(owner)
            children = [ui[key] for key in ui[owner].get("children", []) if key in ui]
            # All siblings are discoverable. Each request is bounded separately.
            for offset in range(0, len(children), 48):
                catalog = [ui_summary(row, parents) for row in children[offset:offset + 48]]
                for row in catalog:
                    row.pop("children", None)
                    row.pop("children_truncated", None)
                payload = {"requirement": self._requirement(rid),
                           "parent": {key: value for key, value in ui_summary(ui[owner], parents).items() if key != "children"},
                           "catalog": catalog, "offset": offset, "total_children": len(children),
                           "selected_ids": sorted(selected), "preferred_screen_matches": matches}
                task_id = f"discovery-{len(self.batches) + 1:05d}"
                record = {"task_id": task_id, "phase": "discovery", "requirement_ids": [rid], "input": payload}
                try:
                    output = self.model.generate_json(schema_name="frontend_discovery",
                        instructions=DISCOVERY_PROMPT, input_payload=payload, output_schema=discovery_schema())
                    allowed = {row["id"] for row in catalog}
                    wanted = set(output["select_ids"]) | set(output["expand_ids"])
                    if wanted - allowed:
                        raise ValueError("Discovery returned IDs outside the directory page")
                    # Select full records within budget; omitted selections remain
                    # reachable through directory IDs and explicit load requests.
                    for key in output["select_ids"]:
                        if sum(len(json.dumps(ui[item], ensure_ascii=False)) for item in selected | {key}) <= 24000:
                            selected.add(key)
                    pending.extend(output["expand_ids"])
                    record.update(output=output, status="DISCOVERED")
                except Exception as exc:
                    record.update(status="FAILED", error=describe_model_error(exc))
                    self.failures.append({"task_id": task_id, "phase": "discovery", "requirement_ids": [rid], "message": record["error"]})
                self.batches.append(record)
                write_json_atomic(self.root / "passes" / f"{task_id}.json", record)

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
                   "api_contracts": [self._api(row) for row in apis] if phase not in {"topology", "ui"} else [],
                   "api_catalog": [{"id": row["id"], "spec": row.get("spec", "")[:180]}
                                   for row in ranked(self.apis, requirement, 24)] if phase not in {"topology", "ui"} else [],
                   "visual_references": [self._visual_context(row, phase) for row in self.visuals
                                         if owners & set(row["requirement_ids"])],
                   "pending_capabilities": [copy.deepcopy(row) for row in self.ir["pending_capabilities"]
                                            if owners & set(row["requirement_ids"])],
                   "prior_task_failures": [row for row in self.failures if owners & set(row["requirement_ids"])]}
        if phase == "topology":
            payload["requirement_catalog"] = [requirement]
        payload["types"] = required_types(payload, [*self.backend_types, *self.ir["types"]])
        return payload

    @staticmethod
    def _visual_context(row: dict[str, Any], phase: str) -> dict[str, Any]:
        if phase == "ui":
            return copy.deepcopy(row)
        keys = ("regions", "layout_cues") if phase == "topology" else ("visible_controls",)
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
        context = local_context(self.ir, rid, self._requirement(rid), set(ids), self.requested_ui.get(rid, set()))
        ids.extend(owner for table, rows in context["existing"].items() for row in rows
                   if table != "ui" or row["id"] in self.requested_ui.get(rid, set())
                   or rid in row.get("requirement_ids", [])
                   for owner in row.get("requirement_ids", []))
        ids.extend(owner for row in context["existing"]["ui"] for event in row.get("events", [])
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
                        semantic_keys = set(row) - {"name", "requirement_ids", "children", "events", "presentation", "visual_reference_ids"}
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
        touched.update(event["id"] for row in output.get("upserts", {}).get("ui", [])
                       for event in row.get("events", []) if phase == "interaction")
        links = trial.setdefault("requirement_links", {})
        for rid in requirements:
            direct = {key for key, row in records.items() if rid in row.get("requirement_ids", [])}
            prior = set(links.get(rid, {}).get("uses", []))
            links[rid] = {"implements": sorted(direct),
                          "uses": sorted(((prior | touched) & records.keys()) - direct)}
        for rid, link in links.items():
            link["implements"] = sorted(key for key, row in records.items() if rid in row.get("requirement_ids", []))
            link["uses"] = sorted((set(link["uses"]) & records.keys()) - set(link["implements"]))
        # A tree edit is committed only when all containment edges remain valid.
        ui = {row["id"]: row for row in trial["ui"]}
        root = trial["root_ui_id"]
        parents = {}
        for row in ui.values():
            for child in row["children"]:
                if child not in ui or child in parents or child == root:
                    raise ValueError(f"Invalid UI containment edge: {row['id']} -> {child}")
                parents[child] = row["id"]
        reached, pending = set(), [root]
        while pending:
            key = pending.pop()
            if key in reached or key not in ui:
                raise ValueError("UI containment must be an acyclic rooted tree")
            reached.add(key)
            pending.extend(ui[key]["children"])
        if reached != set(ui):
            raise ValueError("UI edit leaves unattached nodes; update old and new parents together")
        ids = [row["id"] for table in TABLES for row in trial[table]]
        ids.extend(event["id"] for event in frontend_events(trial))
        if len(ids) != len(set(ids)):
            raise ValueError("Frontend records and embedded events require globally unique IDs")
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
            for _ in range(4):
                requested = output.get("load_ui_ids", [])
                if not requested:
                    break
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
            loaded = {row["id"] for row in payload["existing"]["ui"]}
            known = {row["id"] for row in self.ir["ui"]}
            changed = {row["id"] for row in output.get("upserts", {}).get("ui", [])}
            changed.update(output.get("deletes", {}).get("ui", []))
            if (changed & known) - loaded:
                raise ValueError("Cannot edit unloaded existing UI; request load_ui_ids first")
            record["output"] = output
            record["final_input"] = payload
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
