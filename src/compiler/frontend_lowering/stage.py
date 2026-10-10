"""Deterministic native React/TypeScript lowering; no model calls or prose execution."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..typescript_format import format_typescript
from .runtime import STATE_RUNTIME, UI_RUNTIME
from .types import Types, js, symbol


@dataclass
class ReactLoweringResult:
    sources: dict[str, str] = field(default_factory=dict)
    bindings: list[dict[str, Any]] = field(default_factory=list)
    batches: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    implementation_tasks: list[dict[str, Any]] = field(default_factory=list)
    pending_capabilities: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def report(self) -> dict[str, Any]:
        return {"status": "LOWERED" if self.ok else "FAILED", "errors": self.errors,
                "warnings": self.warnings, "files": sorted(self.sources), "bindings": self.bindings,
                "implementation_tasks": self.implementation_tasks, "implementation_status": "SKELETON",
                "pending_capabilities": self.pending_capabilities}


class FrontendReactLowerer:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()

    def lower(self, ir: dict[str, Any], backend_design: dict[str, Any],
              backend_routes: dict[str, Any], backend_port: int) -> ReactLoweringResult:
        self.result = ReactLoweringResult()
        self.result.pending_capabilities = copy.deepcopy(ir.get("pending_capabilities", []))
        self.result.warnings.extend(f"{gap['id']}: pending integration: {gap['capability']} ({gap['reason']})"
                                    for gap in self.result.pending_capabilities)
        self.ir = ir
        try:
            if ir.get("ir_kind") != "frontend_design":
                raise ValueError("Native frontend lowering requires the lightweight frontend_design IR; regenerate old frontend designs.")
            self.types = Types(ir, backend_design)
            self.ui = {row["id"]: row for row in ir["ui"]}
            self.events = {row["id"]: row for row in ir["events"]}
            self.functions = {row["id"]: row for row in ir["functions"]}
            self.data = {row["id"]: row for row in ir["data"]}
            self.routes = {row["module_id"]: row for row in backend_routes.get("routes", [])}
            self.result.sources["frontend/src/types.ts"] = self.types.source()
            self.result.sources["frontend/src/runtime/state.ts"] = STATE_RUNTIME
            self.result.sources["frontend/src/runtime/ui.tsx"] = UI_RUNTIME
            for data in ir["data"]:
                self._task(data, "frontend/src/types.ts", "data",
                           "Preserve the data contract; implement initialization and updates in its associated event/functions.")
            self._api()
            for row in ir["functions"]:
                self._function(row)
            for row in ir["events"]:
                self._event(row)
            for row in ir["ui"]:
                self._ui(row)
            self._app()
            self.result.sources["frontend/vite.config.ts"] = (
                'import { defineConfig } from "vite";\nimport react from "@vitejs/plugin-react";\n'
                'import tailwindcss from "@tailwindcss/vite";\n'
                f'const proxy = {{ "/api": {{ target: "http://127.0.0.1:{int(backend_port)}", changeOrigin: true }} }};\n'
                'export default defineConfig({ plugins: [react(), tailwindcss()], server: { proxy }, preview: { proxy } });\n'
            )
            self.result.sources = format_typescript(self.result.sources, self.project_root)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            self.result.errors.append(str(exc))
        return self.result

    def _binding(self, row: dict[str, Any], path: str, name: str | None,
                 kind: str, callees: list[str] | None = None) -> None:
        self.result.bindings.append({"entity_id": row["id"], "file": path, "symbol": name,
                                     "kind": kind, "callees": callees or [],
                                     "requirement_ids": row.get("requirement_ids", [])})
        binding = self.result.bindings[-1]
        if kind in {"UI_FUNC", "UI_EVENT", "API_CLIENT"}:
            identity = row["id"].removeprefix("API_CLIENT::") if kind == "API_CLIENT" else row["id"]
            for direction in ("Input", "Output") if kind != "UI_EVENT" else ("Input",):
                binding[direction.lower() + "_type"] = {
                    "type_id": "FRONTEND::" + identity + "::" + direction,
                    "file": "frontend/src/types.ts", "symbol": symbol(direction, identity)}

    def _task(self, row: dict[str, Any], path: str, category: str, description: str) -> None:
        self.result.implementation_tasks.append({"entity_id": row["id"], "file": path,
            "name": row.get("name", row["id"]), "spec": row.get("spec", ""),
            "category": category, "contract": copy.deepcopy(row), "description": description})

    def _function(self, row: dict[str, Any]) -> None:
        name = symbol("fn", row["id"])
        path = f"frontend/src/functions/{name}.ts"
        lines = ['import type * as T from "../types";',
                 'import { unimplemented } from "../runtime/state";',
                 'import type { FunctionContext } from "../runtime/state";',
                 "// Blueprint: " + js(row),
                 f"export async function {name}(context: FunctionContext, input: T.{symbol('Input', row['id'])}): Promise<T.{symbol('Output', row['id'])}> {{"]
        helpers = []
        for call in row["calls"]:
            target = call["target_id"]
            helpers.append(symbol("call", call["id"]))
            # Typed helpers expose actual collaborators but do not execute an
            # underspecified algorithm or guess where local computation belongs.
            lines.extend([
                "// Collaboration: " + js(call),
                f"const {helpers[-1]} = (arguments_: T.{symbol('Input', target)}): Promise<T.{symbol('Output', target)}> => "
                f"context.{'callAPI' if call['kind'] == 'API' else 'callFunction'}({js(target)}, arguments_);",
            ])
        lines += ["void [context, input" + (", " + ", ".join(helpers) if helpers else "") + "];",
                  "// TODO: Implement computation, state writes, outputs and collaboration timing from spec.",
                  f"return unimplemented({js(row['id'])});", "}"]
        self.result.sources[path] = "\n".join(lines) + "\n"
        self._binding(row, path, name, "UI_FUNC", [
            "API_CLIENT::" + call["target_id"] if call["kind"] == "API" else call["target_id"] for call in row["calls"]])
        self._task(row, path, "function", "Implement the typed frontend function using its spec and provided collaboration helpers.")

    def _event(self, row: dict[str, Any]) -> None:
        name, payload_name = symbol("event", row["id"]), symbol("payload", row["id"])
        path = f"frontend/src/events/{name}.ts"
        lines = ['import type * as React from "react";', 'import type * as T from "../types";',
                 'import { argumentsFor, invokeFunction, unimplemented } from "../runtime/state";',
                 'import type { Environment } from "../runtime/state";',
                 "// Blueprint: " + js(row),
                 f"export async function {name}(environment: Environment, input: T.{symbol('Input', row['id'])}): Promise<void> {{",
                 "const results: Record<string, unknown> = {};", "void [environment, input, results, argumentsFor, invokeFunction, unimplemented];"]
        for call in row["handlers"]:
            invocation = f"results[{js(call['id'])}] = await invokeFunction(environment, {js(call['target_id'])}, argumentsFor({js(call['arguments'])}, environment, input, results));"
            if "condition" in call:
                # Prose conditions are real implementation work, never evaluated as code.
                condition_name = symbol("condition", call["id"])
                lines.extend(["// Condition: " + js(call["condition"]),
                    f"const {condition_name}: boolean = unimplemented({js(row['id'] + ':' + call['id'] + ':condition')});",
                    f"if ({condition_name}) {{ {invocation} }}"])
                self._task(row, path, "event_condition", "Implement the business condition on event handler " + call["id"] + ".")
            else:
                lines.append(invocation)
        lines += ["}", f"export function {payload_name}(event?: React.SyntheticEvent<HTMLElement>): T.{symbol('Input', row['id'])} {{", "void event;"]
        fields = row["payload"]
        if not fields:
            lines.append("return {};")
        elif row["name"] == "change" and self.ui[row["ui_id"]]["kind"] in {"input", "choice"} and len(fields) == 1 and fields[0]["type"]["kind"] == "scalar" and fields[0]["type"]["name"] in {"string", "number", "integer", "boolean"}:
            field = fields[0]
            lines.append('if (!event) return unimplemented("Missing change event");')
            lines.append("const target = event.currentTarget as HTMLInputElement | HTMLSelectElement;")
            value = "target.value"
            if field["type"]["name"] == "boolean":
                value = "(target as HTMLInputElement).checked"
            elif field["type"]["name"] in {"number", "integer"}:
                value = "Number(target.value)"
                if field["nullable"]:
                    value = "target.value === '' ? null : " + value
            lines.append(f"return {{ {js(field['semantic_id'])}: {value} }};")
        else:
            lines.append(f"return unimplemented({js(row['id'] + ':payload')});")
            self._task(row, path, "event_payload", "Implement event payload extraction; the IR does not encode the mapping from DOM/item data to these fields.")
        lines.append("}")
        self.result.sources[path] = "\n".join(lines) + "\n"
        self._binding(row, path, name, "UI_EVENT", [call["target_id"] for call in row["handlers"]])

    def _ui(self, row: dict[str, Any]) -> None:
        name = symbol("UI", row["id"])
        path = f"frontend/src/components/{name}.tsx"
        lines = ['import * as React from "react";', 'import { UIBoundary } from "../runtime/ui";',
                 'import type { UIProps } from "../runtime/ui";']
        for child in dict.fromkeys(row["children"]):
            child_name = symbol("UI", child)
            lines.append(f'import {{ {child_name} }} from "./{child_name}";')
        events = [event for event in self.ir["events"] if event["ui_id"] == row["id"]]
        for event in events:
            event_name = symbol("event", event["id"])
            lines.append(f'import {{ {event_name}, {symbol("payload", event["id"])} }} from "../events/{event_name}";')
            if event["name"] not in {"activate", "change", "submit", "enter", "focus", "blur"}:
                self.result.warnings.append(f"{event['id']}: semantic trigger {event['name']} requires a UI adapter implementation.")
                self._task(event, path, "event_trigger", "Map the custom semantic trigger to a React interaction without changing its payload/function contract.")
        node = {key: row[key] for key in ("id", "kind", "route", "bindings", "repeat") if key in row}
        binding = row.get("bindings", {}).get("value", {})
        datatype = self.data.get(binding.get("id"), {}).get("type", {}) if binding.get("kind") == "data" and not binding.get("path") else {}
        if datatype.get("kind") == "scalar":
            node["valueType"] = datatype["name"]
        descendant_routes, pending, seen = [], list(row["children"]), set()
        while pending:
            key = pending.pop()
            if key in seen:
                continue
            seen.add(key)
            child = self.ui.get(key, {})
            if "route" in child:
                descendant_routes.append(child["route"])
            pending.extend(child.get("children", []))
        if descendant_routes:
            node["descendantRoutes"] = descendant_routes
        lines += ["// Blueprint: " + js(row), f"export function {name}({{ environment }}: UIProps) {{", "void React;",
                  "return <UIBoundary environment={environment} node={" + js(node) + "}", "events={["]
        for event in events:
            lines.append("{ id: " + js(event["id"]) + ", name: " + js(event["name"]) +
                         ", payload: " + symbol("payload", event["id"]) + ", execute: (env, input) => " +
                         symbol("event", event["id"]) + "(env, input as TInput_" + symbol("event", event["id"]) + ") },")
        # Event payload types are imported only when used, avoiding unused imports.
        for event in events:
            lines.insert(0, f'import type {{ {symbol("Input", event["id"])} as TInput_{symbol("event", event["id"])} }} from "../types";')
        children = "".join(f"<{symbol('UI', child)} environment={{scope}} />" for child in row["children"])
        lines += ["]} renderChildren={(scope) => { void scope; return <>" + children + "</>; }} />;", "}"]
        self.result.sources[path] = "\n".join(lines) + "\n"
        self._binding(row, path, name, "UI", [*row["children"], *[event["id"] for event in events]])
        self._task(row, path, "ui", "Refine semantic controls, layout, styling and accessibility from the UI spec and optional visual references.")
        for data in self.ir["data"]:
            if data["scope_ui_id"] == row["id"]:
                self._binding(data, "frontend/src/types.ts", None, "UI_DATA")

    def _app(self) -> None:
        lines = ['import * as React from "react";', 'import { configure, createEnvironment, context, registerFunction } from "./runtime/state";',
                 'import { registerAPIs } from "./api/client";',
                 f'import {{ {symbol("UI", self.ir["root_ui_id"])} }} from "./components/{symbol("UI", self.ir["root_ui_id"])}";']
        for row in self.ir["functions"]:
            name = symbol("fn", row["id"])
            lines.append(f'import {{ {name} }} from "./functions/{name}";')
            lines.append(f'import type {{ {symbol("Input", row["id"])} }} from "./types";')
        routes = {row["id"]: row["route"] for row in self.ir["ui"] if "route" in row}
        data = [{key: row[key] for key in ("id", "scope_ui_id", "initial") if key in row} for row in self.ir["data"]]
        lines += ["configure(" + js(data) + ", " + js(routes) + ");", "registerAPIs();", "void [context, registerFunction];"]
        for row in self.ir["functions"]:
            lines.append(f"registerFunction({js(row['id'])}, (environment, input) => {symbol('fn', row['id'])}(context(environment), input as {symbol('Input', row['id'])}));")
        lines += ["export default function App() {", "const [environment] = React.useState(createEnvironment);",
                  f"return <{symbol('UI', self.ir['root_ui_id'])} environment={{environment}} />;", "}"]
        self.result.sources["frontend/src/App.tsx"] = "\n".join(lines) + "\n"
        for row in self.ir["data"]:
            if row["scope_ui_id"] is None:
                self._binding(row, "frontend/src/types.ts", None, "UI_DATA")

    def _api(self) -> None:
        lines = ['import { registerAPI } from "../runtime/state";']
        self.result.sources["frontend/src/api/transport.ts"] = API_TRANSPORT
        registrations = []
        for key, api in self.types.apis.items():
            name = symbol("api", key)
            route = self.routes.get(key)
            inp, out = symbol("Input", key), symbol("Output", key)
            body = ['import type * as T from "../types";', 'import { request } from "./transport";',
                    "void request;", f"export async function {name}(input: T.{inp}, signal?: AbortSignal): Promise<T.{out}> {{"]
            if route:
                request = {field["name"]: {"semantic_id": field["semantic_id"]} for field in api["inputs"]}
                body += ["const wireInput: Record<string, unknown> = {};", "void input;"]
                for wire, field in request.items():
                    body.append(f"if (input[{js(field['semantic_id'])}] !== undefined) wireInput[{js(wire)}] = input[{js(field['semantic_id'])}];")
                body.append(f"const result = await request({js(route['method'])}, {js(route['path'])}, {js(route['input_source'])}, wireInput, signal);")
                body.append("void result;")
                body.append("return {" + ", ".join(js(field["semantic_id"]) + ": result[" + js(field["name"]) + "]" for field in api["outputs"]) + f"}} as T.{out};")
            else:
                body += ["void [input, signal];", f"throw new Error({js('Backend route unavailable: ' + key)});"]
                self.result.warnings.append(f"{key}: backend route unavailable; emitted an explicit API binding stub.")
            body.append("}")
            # Declarations after registerAPIs; function declarations are hoisted.
            lines += [f'import {{ {name} }} from "./{name}";', f'import type {{ {inp} }} from "../types";']
            registrations.append(f"registerAPI({js(key)}, input => {name}(input as {inp}));")
            path = f"frontend/src/api/{name}.ts"
            self.result.sources[path] = "\n".join(body) + "\n"
            owners = list(dict.fromkeys(rid for row in self.ir["functions"]
                if any(call["kind"] == "API" and call["target_id"] == key for call in row["calls"])
                for rid in row["requirement_ids"]))
            self._binding({"id": "API_CLIENT::" + key, "requirement_ids": owners}, path, name, "API_CLIENT", [key])
        lines += ["export function registerAPIs() {", "void registerAPI;", *registrations, "}"]
        self.result.sources["frontend/src/api/client.ts"] = "\n".join(lines) + "\n"


API_TRANSPORT = r'''
export async function request(method: string, route: string, source: string, input: Record<string, unknown>, signal?: AbortSignal): Promise<Record<string, unknown>> {
    const rest = { ...input };
    let path = route.replace(/:([A-Za-z0-9_]+)/g, (_, key: string) => {
        if (rest[key] == null) throw new Error(`Missing API path parameter ${key}`);
        const value = encodeURIComponent(String(rest[key])); delete rest[key]; return value;
    });
    const init: RequestInit = { method, signal, credentials: "same-origin" };
    if (source === "query") {
        const query = new URLSearchParams();
        for (const [key, value] of Object.entries(rest)) if (value !== undefined) query.set(key, value !== null && typeof value === "object" ? JSON.stringify(value) : String(value));
        if (query.size) path += (path.includes("?") ? "&" : "?") + query.toString();
    } else {
        init.headers = { "content-type": "application/json" }; init.body = JSON.stringify(rest);
    }
    const response = await fetch(path, init);
    const text = await response.text();
    if (!response.ok) throw Object.assign(new Error(text || response.statusText), { status: response.status });
    return text ? JSON.parse(text) as Record<string, unknown> : {};
}
'''
