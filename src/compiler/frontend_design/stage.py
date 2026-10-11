"""Postorder frontend passes with explicit direct ownership and reuse associations."""
from __future__ import annotations

from ..ir.backend import module_fields

import copy
import json
import hashlib
import os
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
from .context import TABLES, frontend_ir_traceability, local_context, ranked, required_types, page_screen_matches, context_ui_nodes
from compiler.ir.frontend import frontend_events
from compiler.ir.frontend_storage import read_frontend_ir, write_frontend_ir
from compiler.preprocessing.visual_cache import cache_path, image_key, persist_records, read_cache, consolidate_cache


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
        try:
            self.output_retries = max(0, min(10, int(os.getenv("ARC_STRUCTURED_OUTPUT_RETRY_COUNT", "2"))))
        except ValueError:
            self.output_retries = 2
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
        visual_path = cache_path(self.artifact_root)
        try:
            consolidate_cache(visual_path)
            cached = read_cache(visual_path)
        except (OSError, ValueError):
            cached = {}
            self.warnings.append("Visual analysis cache could not be read; using supplied analyses.")
        if not isinstance(cached, dict):
            cached = {}
        self.cached_visuals = [{**row, "path": path} for path, row in cached.items() if isinstance(row, dict)]
        analyses = {row.get("id", row.get("reference_id")): row for row in
                    [*cached.values(), *(visual_analyses or [])] if isinstance(row, dict)}
        published = []
        for visual in visual_references:
            row = visual if isinstance(visual, dict) else visual.analysis_payload()
            key = row.get("id", row.get("reference_id"))
            path = row.get("path", row.get("source_path"))
            analysis = row.get("analysis") or analyses.get(key, {}).get("analysis", {})
            if analysis and path:
                published.append({**row, "id": key, "source_path": path, "analysis": analysis})
            self.visuals.append({"id": key, "path": row.get("path", row.get("source_path")),
                                 "requirement_ids": row.get("requirement_ids", []),
                                 "analysis": analysis})
            for alias in row.get("aliases", []):
                self.visuals.append({"id": key, "path": alias,
                                     "requirement_ids": row.get("requirement_ids", []), "analysis": analysis})
        persist_records(published, visual_path)
        self.ir = {"ir_kind": "frontend_design", "backend_ref": "../backend/backend_ir.json",
                   "types": [], "root_ui_id": "ui.app", "ui": [{
                       "id": "ui.app", "name": "application_shell", "kind": "group", "spec": "Application UI composition root.",
                       "requirement_ids": [], "children": [], "bindings": {}, "events": [],
                   }], "data": [], "functions": [], "pending_capabilities": [],
                   "requirement_links": {}, "page_roots": {}}
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
        self.page_ui_scope: dict[str, set[str]] = {}
        state_path = self.root / "page_parse_state.json"
        self.page_parse_state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
        self.active_page: dict[str, Any] | None = None
        report_path = self.artifact_root / "design" / "backend" / "report.json"
        backend_report = json.loads(report_path.read_text()) if report_path.is_file() else {}
        gaps = {row["id"]: row for row in self.ir.get("pending_capabilities", [])}
        for row in backend_report.get("pending_capabilities", []):
            gaps.setdefault(row["id"], copy.deepcopy(row))
        self.ir["pending_capabilities"] = list(gaps.values())
        if self.visuals:
            visuals = {row["id"]: row for row in self.ir.get("visual_references", [])}
            visuals.update({row["id"]: {"id": row["id"], "path": row["path"],
                           "analysis_ref": "../visual_cache.json", "analysis_key": image_key(row["path"])}
                            for row in self.visuals})
            self.ir["visual_references"] = list(visuals.values())
        # Design children before their parent; schedule every requirement once.
        for rid in order:
            for page in self.nodes[rid]["pages"]:
                self.active_page = page
                self.requested_ui[rid] = set()
                self._call("ui", [rid], self._payload(rid, "ui"))
            if not self.nodes[rid]["pages"]:
                self._call("ui", [rid], self._payload(rid, "ui"))
            self.active_page = None
            self.requested_ui[rid] = set()
            self.page_ui_scope[rid] = set(self._payload(rid, "data")["loaded_ui_ids"])
            for phase in ("data", "interaction"):
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
        pages = [self.active_page] if self.active_page is not None else self.nodes[rid]["pages"]
        requirement["pages"] = [self._page_context(page) for page in pages]
        matches = page_screen_matches(self.ir, pages)
        context = local_context(self.ir, rid, requirement, set(related),
                                self.requested_ui.get(rid, set()) | self.page_ui_scope.get(rid, set()),
                                screen_matches=matches, page_root_ids={row["screen_id"] for row in matches})
        operations = []
        for page in pages:
            state = self.page_parse_state.get(page["name"], {})
            reference = image_key(page["reference"]) if page.get("reference") else None
            exists = page["name"] in self.ir.get("page_roots", {})
            mode = "NEW_PAGE" if not exists else "BIND_ONLY" if reference is None or reference in state.get("parsed_references", []) else "NEW_PAGE_STATE"
            operation = {"page_name": page["name"], "reference": reference, "mode": mode,
                         "baseline_reference": state.get("baseline_reference")}
            if mode == "NEW_PAGE_STATE":
                baseline = state.get("baseline_ui", [])
                operation["baseline_ui"] = baseline
                if state.get("baseline_reference"):
                    next(row for row in requirement["pages"] if row["name"] == page["name"])["baseline"] = self._page_context(
                        {"name": page["name"], "reference": state["baseline_reference"]})
            operations.append(operation)
        owners = set(related) | {rid}
        attached = {key for owner in owners for key in self.api_owners.get(owner, [])}
        primary = [row for row in self.apis if row["id"] in attached
                   or owners & set(row.get("requirement_ids", []))]
        primary_ids = {row["id"] for row in primary}
        apis = primary + ranked([row for row in self.apis if row["id"] not in primary_ids], requirement, 8)
        payload = {**context, "new_id_prefix": f"{rid}.", "requirement": requirement,
                   "node_role": "NON_LEAF" if self.children[rid] else "LEAF",
                   "api_contracts": [self._api(row) for row in apis] if phase != "ui" else [],
                   "api_catalog": [{"id": row["id"], "spec": row.get("spec", "")[:180]}
                                   for row in ranked(self.apis, requirement, 24)] if phase != "ui" else [],
                   "declared_pages": copy.deepcopy(pages), "page_operations": operations,
                   "missing_pages": [page["name"] for page in pages
                                     if page["name"] not in self.ir.get("page_roots", {})],
                   "pending_capabilities": [copy.deepcopy(row) for row in self.ir["pending_capabilities"]
                                            if owners & set(row["requirement_ids"])],
                   "prior_task_failures": [row for row in self.failures if owners & set(row["requirement_ids"])]}
        payload["types"] = required_types(payload, [*self.backend_types, *self.ir["types"]])
        return payload

    def _page_context(self, page: dict[str, Any]) -> dict[str, Any]:
        result = copy.deepcopy(page)
        result["analysis"] = {}
        if page.get("reference"):
            path = image_key(page["reference"])
            for row in [*self.visuals, *self.cached_visuals]:
                if not isinstance(row, dict) or not row.get("analysis"):
                    continue
                paths = [row.get("path"), row.get("source_path"), *row.get("aliases", [])]
                if any(image_key(key) == path for key in paths if key):
                    result["analysis"] = copy.deepcopy(row["analysis"])
                    break
        return result

    def _requirement(self, rid: str, compact: bool = False) -> dict[str, Any]:
        row = self.nodes[rid]
        result = {"id": rid, **{key: copy.deepcopy(row[key]) for key in
                  ("name", "type", "parent_id", "description", "scenarios", "pages") if key in row}}
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
                                screen_matches=page_screen_matches(self.ir, self.nodes[rid]["pages"]),
                                page_root_ids={row["screen_id"] for row in page_screen_matches(self.ir, self.nodes[rid]["pages"])})
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
                        owners = [] if phase == "ui" else requirements if supplied else []
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
        if phase == "ui":
            ui = {row["id"]: row for row in trial["ui"]}
            page_roots = trial.setdefault("page_roots", {})
            declared = {page["name"] for page in ([self.active_page] if self.active_page is not None
                       else [page for rid in requirements for page in self.nodes[rid]["pages"]])}
            seen = set()
            for assignment in output["page_assignments"]:
                name, key = assignment["name"], assignment["ui_id"]
                if name not in declared or name in seen or ui.get(key, {}).get("kind") != "screen":
                    raise ValueError("Page assignments must uniquely map declared page names to screen nodes")
                if name in page_roots and page_roots[name] != key:
                    raise ValueError(f"Existing page must retain its screen ID: {name}")
                if any(other != name and root == key for other, root in page_roots.items()):
                    raise ValueError("Each page name must have its own screen subtree")
                page_roots[name] = key
                seen.add(name)
            if declared - page_roots.keys():
                raise ValueError(f"UI design must assign the declared pages: {sorted(declared - page_roots.keys())}")
            if any(ui.get(key, {}).get("kind") != "screen" for key in page_roots.values()):
                raise ValueError("Cannot delete a registered page screen")
            if {row["id"] for row in ui.values() if row["kind"] == "screen"} != set(page_roots.values()):
                raise ValueError("Every screen must be registered by a declared page name")
            for rid in requirements:
                links[rid]["uses"] = sorted((set(links[rid]["uses"]) |
                    {page_roots[name] for name in declared}) - set(links[rid]["implements"]))
            for key in output["requirement_ui_ids"]:
                if key not in ui:
                    raise ValueError(f"Cannot bind an unknown UI node: {key}")
                ui[key]["requirement_ids"] = list(dict.fromkeys([*ui[key]["requirement_ids"], *requirements]))
            for rid in requirements:
                links[rid]["implements"] = sorted(key for key, row in records.items() if rid in row.get("requirement_ids", []))
                links[rid]["uses"] = sorted(set(links[rid]["uses"]) - set(links[rid]["implements"]))
        write_frontend_ir(self.root / "frontend_ir.json", trial)
        self.ir = trial

    def _generate(self, kwargs: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
        """Retry malformed JSON or output shapes before applying any design delta."""
        from compiler.backend_design.validation import shape_errors

        feedback: list[str] = []
        for attempt in range(self.output_retries + 1):
            request = {**kwargs, "input_payload": copy.deepcopy(kwargs["input_payload"])}
            if feedback:
                request["input_payload"]["output_format_feedback"] = feedback
            trace = {"attempt": attempt + 1}
            try:
                output = self.model.generate_json(**request)
            except json.JSONDecodeError as exc:
                feedback = [
                    f"Invalid JSON: {exc.msg} at line {exc.lineno}, column {exc.colno}, character {exc.pos}.",
                    "Regenerate the complete JSON object. Check commas, matching brackets and escaped quotes "
                    "inside strings. Return JSON only, without Markdown or code fences; keep prose concise.",
                ]
                trace.update(error=describe_model_error(exc), raw_output=exc.doc,
                             error_position=exc.pos, error_excerpt=exc.doc[max(0, exc.pos - 120):exc.pos + 120])
            else:
                feedback = shape_errors(output, kwargs["output_schema"])
                trace["output"] = output
                if not feedback:
                    trace["status"] = "ACCEPTED"
                    record.setdefault("generation_attempts", []).append(trace)
                    return output
            trace.update(status="REJECTED", errors=feedback)
            record.setdefault("generation_attempts", []).append(trace)
            self.log.info(f"OUTPUT_FORMAT_REJECTED task={record['task_id']} "
                          f"attempt={attempt + 1}/{self.output_retries + 1} errors={feedback}")
        raise ValueError("Frontend output format retries exhausted: " + "; ".join(feedback))

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
            output = self._generate(kwargs, record)
            for _ in range(4):
                requested = output.get("load_ui_ids", [])
                if not requested:
                    break
                if any(output["upserts"].values()) or any(output["deletes"].values()) or output["ui_updates"] or output["implements_ids"] or output["reuse_ids"] or output.get("page_assignments") or output.get("requirement_ui_ids"):
                    raise ValueError("UI context requests must contain an otherwise empty delta")
                known = {row["id"] for row in self.ir["ui"]}
                if set(requested) - known:
                    raise ValueError("Context request contains unknown UI IDs")
                active_pages = [self.active_page] if self.active_page is not None else self.nodes[requirements[0]]["pages"]
                page_ids = {row["screen_id"] for row in page_screen_matches(self.ir, active_pages)}
                ui = {row["id"]: row for row in self.ir["ui"]}
                parents = {child: row["id"] for row in ui.values() for child in row["children"]}
                for key in requested:
                    while key in ui:
                        if ui[key]["kind"] == "screen":
                            if key not in page_ids:
                                raise ValueError("Cannot load a page outside this requirement's declared pages")
                            break
                        key = parents.get(key)
                record["context_requests"].append({"ids": requested, "output": output})
                self.requested_ui[requirements[0]] = set(requested)
                payload = self._payload(requirements[0], phase)
                kwargs["input_payload"] = payload
                output = self._generate(kwargs, record)
            if output.get("load_ui_ids"):
                raise ValueError("UI context request limit exceeded; design a smaller region")
            loaded = set(payload["loaded_ui_ids"])
            known = {row["id"] for row in self.ir["ui"]}
            changed = {row["id"] for row in output.get("upserts", {}).get("ui", [])}
            changed.update(output.get("deletes", {}).get("ui", []))
            changed.update(row["id"] for row in output.get("ui_updates", []))
            if (changed & known) - loaded:
                raise ValueError("Cannot edit unloaded existing UI; request load_ui_ids first")
            if phase == "ui":
                if set(output["requirement_ui_ids"]) - (loaded | {row["id"] for row in output["upserts"]["ui"]}):
                    raise ValueError("Requirement binding must target loaded or newly created UI")
                if payload["page_operations"] and all(row["mode"] == "BIND_ONLY" for row in payload["page_operations"]):
                    if output["upserts"]["ui"] or output["deletes"]["ui"] or output["ui_updates"] or output["page_assignments"]:
                        raise ValueError("Parsed page/reference permits requirement bindings only")
                if any(row["mode"] == "NEW_PAGE_STATE" for row in payload["page_operations"]):
                    if output["deletes"]["ui"] or any(row.get("children", {}).get("remove") for row in output["ui_updates"]):
                        raise ValueError("New page states must preserve previously parsed UI nodes")
            record["output"] = output
            record["final_input"] = payload
            from .delta import materialize_ui_updates
            output = materialize_ui_updates(self.ir, phase, output, loaded, requirements)
            self._merge(phase, output, requirements)
            if phase == "ui":
                self._record_page_parse(payload["page_operations"])
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

    def _record_page_parse(self, operations: list[dict[str, Any]]) -> None:
        ui = {row["id"]: row for row in self.ir["ui"]}
        for operation in operations:
            name, reference = operation["page_name"], operation["reference"]
            state = self.page_parse_state.setdefault(name, {"parsed_references": []})
            state["screen_id"] = self.ir["page_roots"][name]
            evidence = bool(self._page_context({"name": name, "reference": reference})["analysis"])
            if reference and evidence and operation["mode"] != "BIND_ONLY":
                if not state.get("baseline_reference"):
                    state["baseline_reference"] = reference
                    def subtree(key):
                        row = copy.deepcopy(ui[key])
                        row["children"] = [subtree(child) for child in row["children"]]
                        return row
                    state["baseline_ui"] = [subtree(state["screen_id"])]
                state["parsed_references"] = list(dict.fromkeys([*state["parsed_references"], reference]))
        write_json_atomic(self.root / "page_parse_state.json", self.page_parse_state)
