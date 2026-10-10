"""Lower semantic frontend IR to planned React/TypeScript skeletons."""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from compiler.ir.frontend import frontend_events
from compiler.lowering.formatting import format_typescript
from .contracts import JSON_PATH, ModuleContracts
from .planning import plan_ui_units, plan_behavior, api_symbols
from .types import Types, js


ERROR_PATH = "frontend/src/runtime/errors.ts"


@dataclass
class ReactLoweringResult:
    sources: dict[str, str] = field(default_factory=dict)
    bindings: list[dict[str, Any]] = field(default_factory=list)
    batches: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    implementation_tasks: list[dict[str, Any]] = field(default_factory=list)
    pending_capabilities: list[dict[str, Any]] = field(default_factory=list)
    ui_plan: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def report(self) -> dict[str, Any]:
        return {"status": "LOWERED" if self.ok else "FAILED", "errors": self.errors,
                "warnings": self.warnings, "files": sorted(self.sources), "bindings": self.bindings,
                "implementation_tasks": self.implementation_tasks, "implementation_status": "SKELETON",
                "pending_capabilities": self.pending_capabilities, "ui_plan": self.ui_plan}


class FrontendReactLowerer:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()

    def lower(self, ir: dict[str, Any], backend_design: dict[str, Any],
              backend_routes: dict[str, Any], backend_port: int) -> ReactLoweringResult:
        self.result = ReactLoweringResult()
        self.ir = ir
        try:
            if ir.get("ir_kind") != "frontend_design" or "events" in ir:
                raise ValueError("Expected current semantic Frontend IR with UI-owned events")
            self.result.pending_capabilities = copy.deepcopy(ir.get("pending_capabilities", []))
            self.result.warnings.extend(f"{row['id']}: pending integration: {row['capability']} ({row['reason']})"
                                        for row in self.result.pending_capabilities)
            self.types = Types(ir, backend_design)
            self.ui = {row["id"]: row for row in ir["ui"]}
            self.functions = {row["id"]: row for row in ir["functions"]}
            self.events = {row["id"]: row for row in frontend_events(ir)}
            self.data = {row["id"]: row for row in ir["data"]}
            self.result.ui_plan = plan_ui_units(ir)
            self.result.ui_plan.update(plan_behavior(ir, self.result.ui_plan))
            self.plan = self.result.ui_plan
            self.units = {row["id"]: row for row in self.plan["units"]}
            self.api_names = api_symbols(self.types.apis)
            self.api_files = {key: f"frontend/src/api/{name}.ts" for key, name in self.api_names.items()}
            self.routes = {row["module_id"]: row for row in backend_routes.get("routes", [])}
            self.data_owners = {key: self.plan["ui_units"][row["scope_ui_id"] or ir["root_ui_id"]]
                                for key, row in self.data.items()}
            consumers = [(0, self.plan["function_files"][key], row) for key, row in self.functions.items()]
            consumers += [(1, self.api_files[key], row) for key, row in self.types.apis.items()]
            consumers += [(2, self.units[self.data_owners[key]]["file"], row) for key, row in self.data.items()]
            consumers += [(2, self.plan["event_files"][key], row) for key, row in self.events.items()]
            self.contracts = ModuleContracts(self.types, consumers, self.plan["ui_files"][ir["root_ui_id"]])
            self.bodies: dict[str, list[str]] = {}
            self.result.sources[JSON_PATH] = "export type JSONValue = string | number | boolean | null | JSONValue[] | { [key: string]: JSONValue };\n"
            self.result.sources[ERROR_PATH] = 'export class NotImplementedError extends Error {\n  constructor(id: string) { super(`Not implemented: ${id}`); this.name = "NotImplementedError"; }\n}\n'
            for row in ir["functions"]:
                self._function(row)
            for unit in self.plan["units"]:
                self._component(unit)
            for key, row in self.types.apis.items():
                self._api(key, row)
            for path, body in self.bodies.items():
                self.result.sources[path] = self.contracts.render(path, body)
            self._app()
            self.result.sources["frontend/vite.config.ts"] = (
                'import { defineConfig } from "vite";\nimport react from "@vitejs/plugin-react";\n'
                'import tailwindcss from "@tailwindcss/vite";\n'
                f'const proxy = {{ "/api": {{ target: "http://127.0.0.1:{int(backend_port)}", changeOrigin: true }} }};\n'
                'export default defineConfig({ plugins: [react(), tailwindcss()], server: { proxy }, preview: { proxy } });\n'
            )
            self.plan["type_definitions"] = [self.contracts.named_reference(key) for key in sorted(self.types.definitions)]
            self.plan["function_plan"] = [{"id": key, "file": self.plan["function_files"][key],
                "symbol": self.plan["behavior_symbols"][key], "calling_convention": "positional"}
                for key in sorted(self.functions)]
            for binding in self.result.bindings:
                references = self.contracts.references[binding["file"]]
                binding["store_types"] = [*references.values(), *[binding[key]
                    for key in ("context_type", "state_type") if binding.get(key)]]
            self.plan["function_plan"] = [copy.deepcopy(row) for row in self.result.bindings
                                           if row["kind"] == "UI_FUNC"]
            self.result.sources = format_typescript(self.result.sources, self.project_root)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            self.result.errors.append(str(exc))
        return self.result

    def _binding(self, row: dict[str, Any], path: str, name: str | None, kind: str,
                 callees: list[str] | None = None, **metadata: Any) -> None:
        self.result.bindings.append({"entity_id": row["id"], "file": path, "symbol": name,
            "kind": kind, "callees": callees or [], "requirement_ids": row.get("requirement_ids", []),
            "visibility": "local" if kind == "UI_EVENT" else "export", **metadata})

    def _task(self, row: dict[str, Any], path: str, category: str, description: str) -> None:
        self.result.implementation_tasks.append({"entity_id": row["id"], "file": path,
            "name": row.get("name", row["id"]), "spec": row.get("spec", ""),
            "category": category, "contract": copy.deepcopy(row), "description": description})

    def _state(self, path: str, name: str, ids: set[str], identity: str) -> dict[str, str]:
        values = [self.data[key] for key in sorted(ids)]
        self.contracts.use_values(path, values)
        fields = [js(row["id"]) + ": " + self.types.value(row["type"], row["nullable"]) +
                  (" | undefined" if "initial" not in row else "") + ";" for row in values]
        self.contracts.declarations[path].append(f"export interface {name} {{\n" + "\n".join(fields) + "\n}")
        return self.contracts.reference(path, name, identity)

    @staticmethod
    def _stub(identity: str, unused: list[str], *, rendering: bool = False) -> list[str]:
        return ["// @arc-implementation-start", *[f"void {name};" for name in unused],
                "return null;" if rendering else f"throw new NotImplementedError({js(identity)});",
                "// @arc-implementation-end"]

    def _collaborators(self, path: str, calls: list[dict[str, Any]]) -> list[str]:
        names = []
        for call in calls:
            key = call["target_id"]
            if call["kind"] == "FUNC":
                target, name = self.plan["function_files"][key], self.plan["behavior_symbols"][key]
            else:
                target, name = self.api_files[key], self.api_names[key]
            self.contracts.add_import(path, name, target)
            names.append(name)
        return sorted(set(names))

    def _function(self, row: dict[str, Any]) -> None:
        key = row["id"]
        path, name = self.plan["function_files"][key], self.plan["behavior_symbols"][key]
        stem = name[:1].upper() + name[1:]
        input_ref = self.contracts.fields(path, stem + "Input", row["inputs"], key + "::Input")
        output_ref = self.contracts.fields(path, stem + "Output", row["outputs"], key + "::Output") if row["outputs"] else None
        context_ref = None
        state_ids = set(row["reads"]) | set(row["writes"])
        if state_ids:
            state_ref = self._state(path, stem + "State", state_ids, key + "::State")
            self.contracts.declarations[path].append(f"export interface {stem}Context {{\n  readonly state: {state_ref['symbol']};\n  updateState?(updates: Partial<{state_ref['symbol']}>): void;\n}}")
            context_ref = self.contracts.reference(path, stem + "Context", key + "::Context")
        self.contracts.add_import(path, "NotImplementedError", ERROR_PATH)
        collaborators = self._collaborators(path, row["calls"])
        parameters, signature, names = [], [], []
        for item in row["inputs"]:
            identifier = item["name"]
            if not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", identifier) or identifier in names or identifier in {"_context", "arguments", "eval", "await", "yield", "default", "class", "function", "import", "export", "return", "new", "var", "let", "const", "this", "super", "null", "true", "false", "delete", "in", "instanceof", "typeof", "void", "throw", "case", "switch", "if", "else", "for", "while", "do", "break", "continue", "try", "catch", "finally", "with", "debugger", "enum", "extends", "implements", "interface", "package", "private", "protected", "public", "static"}:
                raise ValueError(f"Invalid frontend parameter name: {identifier}")
            value = self.types.value(item["type"], item["nullable"])
            signature.append(f"{identifier}: {value}" + (" | undefined = undefined" if not item["required"] else ""))
            names.append(identifier)
            parameters.append(copy.deepcopy(item))
        if context_ref:
            signature.append(f"_context?: {context_ref['symbol']}")
            names.append("_context")
        returns = output_ref["symbol"] if output_ref else "void"
        body = ["/** " + row["spec"].replace("*/", "* /") + " */",
                f"export async function {name}({', '.join(signature)}): Promise<{returns}> {{",
                *self._stub(key, [*names, *collaborators]), "}"]
        self.bodies[path] = body
        self._binding(row, path, name, "UI_FUNC", ["API_CLIENT::" + call["target_id"] if call["kind"] == "API" else call["target_id"] for call in row["calls"]],
                      input_type=input_ref, output_type=output_ref, context_type=context_ref,
                      parameters=parameters, calling_convention="positional")
        self._task(row, path, "function", "Implement the positional function body; preserve parameter order, types and return contract.")

    def _component(self, unit: dict[str, Any]) -> None:
        path, name = unit["file"], unit["symbol"]
        self.contracts.imports[path][True, "react"].update({"ReactElement", "SyntheticEvent"} if unit["event_ids"] else {"ReactElement"})
        state_ids = {key for key, owner in self.data_owners.items() if owner == unit["id"]}
        # Cross-region data is an explicit input contract, not hidden runtime state.
        from compiler.frontend_design.context import value_references
        for key in unit["ui_ids"]:
            state_ids |= value_references(self.ui[key], "data")
        state_ref = self._state(path, name + "State", state_ids, unit["id"] + "::State")
        props_lines = [f"state?: {state_ref['symbol']};"]
        handler_lines, handler_names = [], []
        for key in unit["event_ids"]:
            event = self.events[key]
            handler = self.plan["behavior_symbols"][key]
            stem = handler[:1].upper() + handler[1:]
            payload_ref = self.contracts.fields(path, stem + "Input", event["payload"], key + "::Input")
            props_lines.append(f"{handler}?: (input: {payload_ref['symbol']}) => Promise<void>;")
            self.contracts.add_import(path, "NotImplementedError", ERROR_PATH)
            collaborators = self._collaborators(path, event["handlers"])
            handler_lines += [f"async function {handler}(input: {payload_ref['symbol']}, event?: SyntheticEvent<HTMLElement>): Promise<void> {{",
                              *self._stub(key, ["input", "event", *collaborators]), "}"]
            handler_names.append(handler)
            self._binding(event, path, handler, "UI_EVENT", [call["target_id"] for call in event["handlers"]],
                          input_type=payload_ref, calling_convention="event_payload", component_id=unit["id"])
            self._task(event, path, "event", "Implement payload extraction, conditions and frontend function calls in the component handler.")
        self.contracts.declarations[path].append(f"export interface {name}Props {{\n" + "\n".join(props_lines) + "\n}")
        props_ref = self.contracts.reference(path, name + "Props", unit["id"] + "::Props")
        child_names = []
        for child in unit["child_component_ids"]:
            child_unit = self.units[child]
            self.contracts.add_import(path, child_unit["symbol"], child_unit["file"])
            child_names.append(child_unit["symbol"])
        self.bodies[path] = [f"export function {name}(props: {name}Props): ReactElement | null {{",
            *handler_lines, *self._stub(unit["id"], ["props", *handler_names, *child_names], rendering=True), "}"]
        unit.update(props_type=props_ref, state_type=state_ref)
        for key in unit["ui_ids"]:
            row = self.ui[key]
            self._binding(row, path, name, "UI", [*row["children"], *[event["id"] for event in row["events"]]],
                          props_type=props_ref, component_id=unit["id"], calling_convention="react_props")
            self._task(row, path, "ui", "Implement this semantic UI inside its planned component; preserve shared subtree responsibilities.")
        for key, owner in self.data_owners.items():
            if owner == unit["id"]:
                self._binding(self.data[key], path, state_ref["symbol"], "UI_DATA", state_type=state_ref)
                self._task(self.data[key], path, "data", "Implement state lifecycle and updates in the owning component; the interface defines its shape only.")

    def _api(self, key: str, row: dict[str, Any]) -> None:
        path, name = self.api_files[key], self.api_names[key]
        stem = name[:1].upper() + name[1:]
        input_ref = self.contracts.fields(path, stem + "Input", row["inputs"], key + "::Input")
        output_ref = self.contracts.fields(path, stem + "Output", row["outputs"], key + "::Output")
        self.contracts.add_import(path, "NotImplementedError", ERROR_PATH)
        self.bodies[path] = [f"export async function {name}(input: {input_ref['symbol']}, signal?: AbortSignal): Promise<{output_ref['symbol']}> {{",
                             *self._stub("API_CLIENT::" + key, ["input", "signal"]), "}"]
        owners = sorted({rid for fn in self.functions.values() for call in fn["calls"] if call["kind"] == "API" and call["target_id"] == key for rid in fn["requirement_ids"]})
        metadata = {"id": "API_CLIENT::" + key, "requirement_ids": owners, "spec": row["spec"]}
        self._binding(metadata, path, name, "API_CLIENT", [key], input_type=input_ref, output_type=output_ref,
                      calling_convention="input_object", route=copy.deepcopy(self.routes.get(key)))
        self._task(metadata, path, "api_client", "Implement HTTP request/response mapping from the supplied backend API route contract.")
        if key not in self.routes:
            self.result.warnings.append(f"{key}: backend route unavailable")
        self.plan.setdefault("api_plan", []).append({"id": key, "file": path, "symbol": name,
            "path": row["path"], "method": row["method"], "request": copy.deepcopy(row["request"]),
            "response": copy.deepcopy(row["response"]), "status_code": row["status_code"], "content_type": row["content_type"]})

    def _app(self) -> None:
        root = self.units[self.ir["root_ui_id"]]
        from .contracts import relative
        self.result.sources["frontend/src/App.tsx"] = f'''import type {{ ReactElement }} from "react";
import {{ {root['symbol']} }} from {js(relative('frontend/src/App.tsx', root['file']))};

export default function App(): ReactElement {{
  return <{root['symbol']} />;
}}
'''
        self.plan["routes"] = [{"ui_id": row["id"], "path": row["route"],
            "component_id": self.plan["ui_units"][row["id"]], "file": self.plan["ui_files"][row["id"]]}
            for row in self.ir["ui"] if "route" in row]
