from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any


FIELD_TYPES = {"string", "integer", "number", "boolean", "date", "datetime", "json"}
ALLOWED_LINKS = {
    ("PAGE", "API"),
    ("API", "FUNCTION"),
    ("FUNCTION", "FUNCTION"),
    ("FUNCTION", "REPOSITORY"),
}
SYMBOL_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
MODULE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
HTTP_ROUTE_PATTERN = re.compile(r"^(GET|POST|PUT|PATCH|DELETE) /[^ ]*$")


def assign_module_files(modules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Assign stable target paths without asking the model to design layout."""

    result: list[dict[str, Any]] = []
    for module in modules:
        segments = module["id"].split(".")
        domain = segments[0]
        name = "-".join(segments[1:])
        kind = module["kind"]
        if kind == "PAGE":
            file_name = f"frontend/src/pages/{domain}/{name}.tsx"
        elif kind == "API":
            file_name = f"backend/src/http/{domain}/{name}.route.ts"
        elif kind == "FUNCTION":
            file_name = f"backend/src/modules/{domain}/{name}.ts"
        else:
            file_name = f"backend/src/repositories/{domain}/{name}.repository.ts"
        result.append({**module, "file": file_name})
    return result


class DesignValidator:
    """Validate flow-derived module interfaces, database edges, and requirement coverage."""

    def validate(
        self,
        design: dict[str, Any],
        requirement_ir: dict[str, Any],
        database_schema: dict[str, Any],
        dependencies: dict[str, Any],
    ) -> list[str]:
        errors: list[str] = []
        requirement_ids = set(requirement_ir.get("atomic_units", []))
        database_entities = {
            str(entity.get("key")): entity
            for entity in database_schema.get("entities", [])
            if entity.get("key")
        }
        if design.get("schema_version") != 4:
            errors.append("Design IR schema_version must be 4")
        if "types" in design:
            errors.append("Design IR must not contain a global types registry")

        modules = self._modules(design.get("modules", []), requirement_ids, database_entities, errors)
        links, graph, mapped_inputs = self._links(
            design.get("links", []), requirement_ids, modules, errors
        )
        if _has_cycle(graph):
            errors.append("Module dataflow graph contains a cycle")
        self._requirements(
            design.get("requirements", []),
            requirement_ids,
            modules,
            links,
            graph,
            mapped_inputs,
            database_schema,
            dependencies,
            errors,
        )
        return errors

    @staticmethod
    def _modules(
        raw_modules: list[dict[str, Any]],
        requirement_ids: set[str],
        database_entities: dict[str, dict[str, Any]],
        errors: list[str],
    ) -> dict[str, dict[str, Any]]:
        modules: dict[str, dict[str, Any]] = {}
        paths: set[str] = set()
        routes: set[str] = set()
        for module in raw_modules:
            module_id = str(module.get("id", ""))
            if not MODULE_ID_PATTERN.fullmatch(module_id):
                errors.append(f"Invalid module id: {module_id}")
            if module_id in modules:
                errors.append(f"Duplicate module: {module_id}")
                continue
            modules[module_id] = module
            kind = str(module.get("kind", ""))
            if module.get("owner") not in requirement_ids:
                errors.append(f"Unknown module owner: {module_id}")
            file_name = str(module.get("file", ""))
            normalized_path = str(PurePosixPath(file_name)).lower()
            if not file_name or normalized_path in paths:
                errors.append(f"Invalid or duplicate module file: {file_name}")
            paths.add(normalized_path)

            route = str(module.get("route", ""))
            if kind == "PAGE" and (not route.startswith("/") or " " in route):
                errors.append(f"Invalid PAGE route: {module_id} -> {route}")
            elif kind == "API" and not HTTP_ROUTE_PATTERN.fullmatch(route):
                errors.append(f"Invalid API route: {module_id} -> {route}")
            elif kind not in {"PAGE", "API"} and route:
                errors.append(f"Only PAGE/API modules may declare routes: {module_id}")
            if route and route in routes:
                errors.append(f"Duplicate route: {route}")
            if route:
                routes.add(route)

            input_fields = _interface_fields(module, "input", errors)
            _interface_fields(module, "output", errors)
            _interface_fields(module, "local", errors)
            reads = _string_values(module, "reads", errors)
            writes = _string_values(module, "writes", errors)
            generates = _string_values(module, "generates", errors)
            access_ids = _string_values(module, "access_ids", errors)
            effect_ids = _string_values(module, "effect_ids", errors)
            realized_access_ids = _string_values(module, "realized_access_ids", errors)
            realized_effect_ids = _string_values(module, "realized_effect_ids", errors)
            if access_ids != realized_access_ids:
                errors.append(f"Database read responsibilities are not fully realized: {module_id}")
            if effect_ids != realized_effect_ids:
                errors.append(f"Database write responsibilities are not fully realized: {module_id}")
            if kind != "REPOSITORY" and (reads or writes or generates):
                errors.append(f"Only REPOSITORY modules may access database entities: {module_id}")
            for entity in sorted(reads | writes):
                if entity not in database_entities:
                    hint = (
                        " (reads/writes require an entity key such as 'account', not entity.field)"
                        if "." in entity
                        else ""
                    )
                    errors.append(f"Unknown database entity: {module_id} -> {entity}{hint}")

            generated_by_entity: dict[str, set[str]] = {}
            for generated in generates:
                entity_name, separator, field_name = str(generated).partition(".")
                entity = database_entities.get(entity_name)
                available = (
                    {str(field.get("name", "")) for field in entity.get("fields", [])}
                    | {str(relation.get("name", "")) for relation in entity.get("relations", [])}
                    if entity
                    else set()
                )
                if not separator or entity_name not in writes or field_name not in available:
                    errors.append(f"Invalid generated database field: {module_id} -> {generated}")
                else:
                    generated_by_entity.setdefault(entity_name, set()).add(field_name)
            for entity_name in sorted(writes):
                entity = database_entities.get(entity_name)
                if entity is None:
                    continue
                required = {
                    str(field.get("name", ""))
                    for field in entity.get("fields", [])
                    if field.get("required")
                    and not field.get("primary_key")
                    and field.get("origin") != "RELATIONSHIP"
                    and field.get("type") != "foreign_key"
                } | {
                    str(relation.get("name", ""))
                    for relation in entity.get("relations", [])
                    if relation.get("required")
                }
                missing = sorted(required - set(input_fields) - generated_by_entity.get(entity_name, set()))
                if missing:
                    errors.append(
                        f"Repository input does not cover required fields: {module_id} -> {', '.join(missing)}"
                    )
        return modules

    @staticmethod
    def _links(
        raw_links: list[dict[str, Any]],
        requirement_ids: set[str],
        modules: dict[str, dict[str, Any]],
        errors: list[str],
    ) -> tuple[list[dict[str, Any]], dict[str, set[str]], dict[str, set[str]]]:
        links: list[dict[str, Any]] = []
        graph: dict[str, set[str]] = {module_id: set() for module_id in modules}
        mapped_inputs: dict[str, set[str]] = {}
        for link in raw_links:
            link_type = str(link.get("type", ""))
            source_module_id = str(link.get("from", ""))
            target_module_id = str(link.get("to", ""))
            links.append(link)
            sources = link.get("sources", [])
            if not isinstance(sources, list) or not sources or any(source not in requirement_ids for source in sources):
                errors.append(f"Invalid link requirement sources: {source_module_id} -> {target_module_id}")
            if link_type in {"READ_DB", "WRITE_DB"}:
                module_id = target_module_id if link_type == "READ_DB" else source_module_id
                database_ref = source_module_id if link_type == "READ_DB" else target_module_id
                if module_id not in modules or not database_ref.startswith("db."):
                    errors.append(f"Unresolved database link endpoint: {source_module_id} -> {target_module_id}")
                    continue
                module = modules[module_id]
                if module.get("kind") != "REPOSITORY":
                    errors.append(f"Only REPOSITORY may have a database link: {module_id}")
                entity = database_ref.removeprefix("db.")
                allowed = set(module.get("reads", [])) if link_type == "READ_DB" else set(module.get("writes", []))
                if entity not in allowed:
                    errors.append(f"Database link is not declared by module: {module_id} -> {entity}")
                responsibility_id = str(link.get("responsibility_id", ""))
                realized_key = "realized_access_ids" if link_type == "READ_DB" else "realized_effect_ids"
                if responsibility_id not in set(module.get(realized_key, [])):
                    errors.append(
                        f"Database link has an unknown responsibility: {module_id} -> {responsibility_id}"
                    )
                continue
            if link_type not in {"CALL", "RETURN"}:
                errors.append(f"Invalid link type: {link_type}")
                continue
            if source_module_id not in modules or target_module_id not in modules:
                errors.append(f"Unresolved link endpoint: {source_module_id} -> {target_module_id}")
                continue
            source_module = modules[source_module_id]
            target_module = modules[target_module_id]
            call_kinds = (
                (source_module.get("kind"), target_module.get("kind"))
                if link_type == "CALL"
                else (target_module.get("kind"), source_module.get("kind"))
            )
            if call_kinds not in ALLOWED_LINKS:
                errors.append(
                    f"Illegal {link_type} module link: {source_module.get('kind')} -> "
                    f"{target_module.get('kind')} ({source_module_id} -> {target_module_id})"
                )
            if link_type == "CALL":
                graph[source_module_id].add(target_module_id)
                source_fields = {
                    **_field_map(source_module.get("input", [])),
                    **_field_map(source_module.get("local", [])),
                }
                target_fields = _field_map(target_module.get("input", []))
                source_directions = ("input", "local")
                target_direction = "input"
            else:
                source_fields = _field_map(source_module.get("output", []))
                target_fields = {
                    **_field_map(target_module.get("local", [])),
                    **_field_map(target_module.get("output", [])),
                }
                source_directions = ("output",)
                target_direction = ("local", "output")
            edge_targets: set[str] = set()
            for mapping in link.get("mapping", []):
                source_ref = str(mapping.get("source", ""))
                target_ref = str(mapping.get("target", ""))
                source_name = _qualified_name_any(source_ref, source_module_id, source_directions)
                target_name = _qualified_name_any(
                    target_ref,
                    target_module_id,
                    (target_direction,) if isinstance(target_direction, str) else target_direction,
                )
                if source_name is None or source_name not in source_fields:
                    errors.append(f"Unknown mapping source: {source_ref}")
                    continue
                if target_name is None or target_name not in target_fields:
                    errors.append(f"Unknown mapping target: {target_ref}")
                    continue
                if target_name in edge_targets:
                    errors.append(f"Duplicate mapping target: {target_ref}")
                edge_targets.add(target_name)
                if link_type == "CALL":
                    mapped_inputs.setdefault(target_module_id, set()).add(target_name)
                source_type = str(source_fields[source_name].get("type", ""))
                target_type = str(target_fields[target_name].get("type", ""))
                if not _assignable(source_type, target_type):
                    errors.append(f"Mapping type mismatch: {source_ref} ({source_type}) -> {target_ref} ({target_type})")
        return links, graph, mapped_inputs

    @staticmethod
    def _requirements(
        raw_records: list[dict[str, Any]],
        requirement_ids: set[str],
        modules: dict[str, dict[str, Any]],
        links: list[dict[str, Any]],
        graph: dict[str, set[str]],
        mapped_inputs: dict[str, set[str]],
        database_schema: dict[str, Any],
        dependencies: dict[str, Any],
        errors: list[str],
    ) -> None:
        records: dict[str, dict[str, Any]] = {}
        entrypoint_modules: set[str] = set()
        for record in raw_records:
            node_id = str(record.get("id", ""))
            if node_id in records:
                errors.append(f"Duplicate requirement coverage: {node_id}")
            records[node_id] = record
            entrypoint_modules.update(str(item) for item in record.get("entrypoints", []))
        for missing in sorted(requirement_ids - set(records)):
            errors.append(f"Missing requirement coverage: {missing}")
        for extra in sorted(set(records) - requirement_ids):
            errors.append(f"Unexpected requirement coverage: {extra}")

        for module_id, module in modules.items():
            if module_id in entrypoint_modules:
                continue
            required = {
                str(field.get("name", ""))
                for field in module.get("input", [])
                if field.get("required")
            }
            missing = sorted(required - mapped_inputs.get(module_id, set()))
            if missing:
                errors.append(f"Module input is not fully mapped: {module_id} -> {', '.join(missing)}")

        database_domains = {
            str(entity.get("key")): set(entity.get("sources", []))
            for entity in database_schema.get("entities", [])
        }
        for node_id, record in records.items():
            entrypoints = [str(item) for item in record.get("entrypoints", [])]
            outcomes = [str(item) for item in record.get("outcomes", [])]
            if not entrypoints:
                errors.append(f"Requirement has no entrypoint: {node_id}")
            if not outcomes:
                errors.append(f"Requirement has no outcome: {node_id}")
            owned_links = [link for link in links if node_id in link.get("sources", [])]
            if not owned_links:
                errors.append(f"Requirement has no dataflow links: {node_id}")
            _validate_call_return_order(node_id, owned_links, errors)
            for module_id in entrypoints:
                module = modules.get(module_id)
                if module is None or module.get("kind") not in {"PAGE", "API"}:
                    errors.append(f"Invalid requirement entrypoint: {node_id} -> {module_id}")
            for module_id in outcomes:
                if module_id not in modules:
                    errors.append(f"Invalid requirement outcome: {node_id} -> {module_id}")
            for entrypoint in entrypoints:
                reachable = _reachable(entrypoint, graph)
                for outcome in outcomes:
                    if outcome not in reachable:
                        errors.append(f"Unreachable requirement outcome: {node_id} ({entrypoint} -> {outcome})")

            allowed_requirements = {node_id} | _dependency_closure(node_id, dependencies)
            allowed_domains = {
                entity
                for entity, sources in database_domains.items()
                if sources & allowed_requirements
            }
            if not set(record.get("data", [])) <= allowed_domains:
                errors.append(f"Requirement contains unrelated data domains: {node_id}")
            for link in owned_links:
                caller = modules.get(str(link.get("from", "")))
                callee = modules.get(str(link.get("to", "")))
                if caller and caller.get("owner") not in allowed_requirements:
                    errors.append(f"Requirement links from an unrelated Module: {node_id}")
                if callee and callee.get("owner") not in allowed_requirements:
                    errors.append(f"Requirement links to an unrelated Module: {node_id}")
                called_module = callee if link.get("type") == "CALL" else caller
                if called_module and called_module.get("kind") == "REPOSITORY":
                    accessed = set(called_module.get("reads", [])) | set(called_module.get("writes", []))
                    if not accessed <= allowed_domains:
                        errors.append(f"Requirement accesses unrelated database domain: {node_id}")


def _validate_call_return_order(
    node_id: str,
    links: list[dict[str, Any]],
    errors: list[str],
) -> None:
    """Validate an ordered, properly nested sequence of invocation events."""

    calls: list[tuple[str, str]] = []
    for link in links:
        link_type = link.get("type")
        source_module = str(link.get("from", ""))
        target_module = str(link.get("to", ""))
        if link_type == "CALL":
            calls.append((source_module, target_module))
        elif link_type == "RETURN":
            expected = calls.pop() if calls else None
            if expected != (target_module, source_module):
                errors.append(
                    f"Unmatched RETURN in requirement {node_id}: "
                    f"{source_module} -> {target_module}"
                )
    for caller, callee in reversed(calls):
        errors.append(
            f"CALL has no matching RETURN in requirement {node_id}: "
            f"{caller} -> {callee}"
        )


def _interface_fields(
    module: dict[str, Any],
    direction: str,
    errors: list[str],
) -> dict[str, dict[str, Any]]:
    module_id = str(module.get("id", ""))
    raw_fields = module.get(direction)
    if not isinstance(raw_fields, list):
        errors.append(f"Module {direction} must be an array: {module_id}")
        return {}
    fields: dict[str, dict[str, Any]] = {}
    for field in raw_fields:
        name = str(field.get("name", "")) if isinstance(field, dict) else ""
        if not SYMBOL_PATTERN.fullmatch(name):
            errors.append(f"Invalid {direction} field: {module_id}.{name}")
        if name in fields:
            errors.append(f"Duplicate {direction} field: {module_id}.{name}")
        if not isinstance(field, dict) or field.get("type") not in FIELD_TYPES:
            errors.append(f"Invalid {direction} field type: {module_id}.{name}")
            continue
        if not isinstance(field.get("required"), bool):
            errors.append(f"Invalid {direction} required flag: {module_id}.{name}")
        fields[name] = field
    return fields


def _string_values(
    module: dict[str, Any],
    key: str,
    errors: list[str],
) -> set[str]:
    """Read a string-array property without letting malformed model data crash validation."""

    module_id = str(module.get("id", ""))
    raw_values = module.get(key)
    if not isinstance(raw_values, list):
        errors.append(f"Module {key} must be an array: {module_id}")
        return set()
    invalid_count = sum(not isinstance(value, str) for value in raw_values)
    if invalid_count:
        errors.append(f"Module {key} must contain only strings: {module_id}")
    return {value for value in raw_values if isinstance(value, str)}


def _field_map(raw_fields: Any) -> dict[str, dict[str, Any]]:
    return {
        str(field.get("name")): field
        for field in raw_fields
        if isinstance(field, dict) and field.get("name")
    } if isinstance(raw_fields, list) else {}


def _qualified_name(reference: str, module_id: str, direction: str) -> str | None:
    prefix = f"{module_id}.{direction}."
    if not reference.startswith(prefix):
        return None
    name = reference[len(prefix):]
    return name if SYMBOL_PATTERN.fullmatch(name) else None


def _qualified_name_any(reference: str, module_id: str, directions: tuple[str, ...]) -> str | None:
    for direction in directions:
        name = _qualified_name(reference, module_id, direction)
        if name is not None:
            return name
    return None


def _assignable(source: str, target: str) -> bool:
    return source == target or (source == "integer" and target == "number")


def _has_cycle(graph: dict[str, set[str]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        if any(visit(child) for child in graph.get(node, set())):
            return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in graph)


def _reachable(start: str, graph: dict[str, set[str]]) -> set[str]:
    result = {start}
    pending = [start]
    while pending:
        current = pending.pop()
        for target in graph.get(current, set()):
            if target not in result:
                result.add(target)
                pending.append(target)
    return result


def _dependency_closure(node_id: str, dependencies: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    pending = list(dependencies.get(node_id, []))
    while pending:
        current = str(pending.pop())
        if current in result:
            continue
        result.add(current)
        pending.extend(dependencies.get(current, []))
    return result
