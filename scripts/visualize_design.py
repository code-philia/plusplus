from __future__ import annotations

"""Render compact Stage 2 design symbol tables as an SVG call graph."""

import argparse
import json
import math
import textwrap
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any

CARD_WIDTH = 520
CARD_GAP = 64
LAYER_GAP = 150
SECTION_GAP = 140
MARGIN = 56
LINE_HEIGHT = 18
COLORS = {
    "CONTRACT": ("#F2F4F7", "#59636E", "#20262E"),
    "API": ("#E8F1FF", "#3973B9", "#15385F"),
    "FUNC": ("#FFF4D6", "#B88627", "#5B410E"),
    "DB": ("#E5F5EA", "#398857", "#174629"),
}

@dataclass
class Card:
    id: str
    kind: str
    requirement: str
    title: str
    lines: list[tuple[str, str]]
    layer: int
    x: float = 0
    y: float = 0
    width: float = CARD_WIDTH
    height: float = 0

    def measure(self) -> None:
        total = 58
        for text, role in self.lines:
            width = 66 if role != "muted" else 72
            total += max(1, len(wrap(text, width))) * LINE_HEIGHT
            if role == "section":
                total += 9
        self.height = max(150, total + 20)


def wrap(value: Any, width: int = 66) -> list[str]:
    text = str(value or "-").strip() or "-"
    return textwrap.wrap(text, width=width, break_long_words=False, break_on_hyphens=False) or ["-"]


def read_list(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise SystemExit(f"{path} must contain a JSON list of objects")
    return value


def resolve_design_dir(value: Path) -> Path:
    root = value.expanduser().resolve()
    for candidate in (root, root / "design", root / ".arc" / "design"):
        if all((candidate / name).is_file() for name in (
            "requirement_contracts.json", "api_modules.json", "function_modules.json", "db_modules.json"
        )):
            return candidate
    raise SystemExit(f"Cannot find Stage 2 design artifacts below {root}")


def load(design_dir: Path) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    contracts = read_list(design_dir / "requirement_contracts.json")
    modules: dict[str, dict[str, Any]] = {}
    for filename, kind in (
        ("api_modules.json", "API"),
        ("function_modules.json", "FUNC"),
        ("db_modules.json", "DB"),
    ):
        for source in read_list(design_dir / filename):
            item = dict(source)
            module_id = str(item.get("id") or "")
            if not module_id or module_id in modules:
                raise SystemExit(f"Invalid or duplicate module id: {module_id}")
            item["kind"] = kind
            item["owner_requirement"] = module_id.split("::", 1)[0]
            item["display_name"] = module_id.split("::", 1)[-1]
            modules[module_id] = item
    for module in modules.values():
        module.setdefault("callers", [])
        module.setdefault("callees", [])

    calls: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str]] = set()

    def add_call(caller_id: str, callee_id: str, order: int) -> None:
        if caller_id not in modules or callee_id not in modules:
            return
        edge = (caller_id, callee_id)
        if edge in seen_edges:
            return
        seen_edges.add(edge)
        callee = modules[callee_id]
        calls.append({
            "caller": caller_id,
            "callee": callee_id,
            "order": order,
            "inputs": [
                str(item.get("semantic_id") or item.get("name") or "?")
                for item in callee.get("inputs", [])
                if isinstance(item, dict)
            ],
            "outputs": [
                str(item.get("semantic_id") or item.get("name") or "?")
                for item in callee.get("outputs", [])
                if isinstance(item, dict)
            ],
        })

    # The ordered callees list is the persisted source of truth for calls.
    for module_id, module in modules.items():
        for order, callee_id in enumerate(module.get("callees", []), start=1):
            add_call(module_id, str(callee_id), order)

    # Complete hand-written artifacts that contain only the reverse reference.
    for callee_id, module in modules.items():
        for caller_id in module.get("callers", []):
            caller_key = str(caller_id)
            next_order = 1 + sum(1 for call in calls if call["caller"] == caller_key)
            add_call(caller_key, callee_id, next_order)

    for call in calls:
        caller = modules[str(call["caller"])]
        callee = modules[str(call["callee"])]
        caller["callees"] = list(dict.fromkeys([*caller["callees"], str(call["callee"])]))
        callee["callers"] = sorted(set(callee["callers"]) | {str(call["caller"])})
        if caller["kind"] == "API" and callee["kind"] != "FUNC":
            raise SystemExit(f"API may call only FUNC: {call['caller']} -> {call['callee']}")
        if caller["kind"] == "FUNC" and callee["kind"] not in {"FUNC", "DB"}:
            raise SystemExit(f"FUNC may call only FUNC or DB: {call['caller']} -> {call['callee']}")
        if caller["kind"] == "DB":
            raise SystemExit(f"DB must be a leaf module: {call['caller']} -> {call['callee']}")
    return contracts, modules, calls


def field_text(field: dict[str, Any]) -> str:
    req = "required" if field.get("required", True) else "optional"
    return f"{field.get('semantic_id', '?')} : {field.get('type', '?')} ({req})"


def effect_text(effect: dict[str, Any]) -> str:
    prefix = f"{effect.get('id', '?')} : {effect.get('operation', '?')} {effect.get('target', '')}".strip()
    action = effect.get("action")
    if not isinstance(action, dict):
        return prefix
    if action.get("kind") == "DATABASE":
        return f"{prefix} | {action.get('statement', 'SQL')}"
    detail = action.get("target") or ""
    return f"{prefix} | {action.get('kind', 'STATE')}/{action.get('command', 'MUTATE')} {detail}".strip()


def module_card(module: dict[str, Any], outgoing: list[dict[str, Any]], layer: int) -> Card:
    lines: list[tuple[str, str]] = [(str(module.get("spec") or "-"), "body")]
    lines.append(("INPUTS", "section"))
    lines.extend((field_text(item), "body") for item in module.get("inputs", []))
    if not module.get("inputs"):
        lines.append(("-", "muted"))
    lines.append(("OUTPUTS", "section"))
    lines.extend((field_text(item), "body") for item in module.get("outputs", []))
    if not module.get("outputs"):
        lines.append(("-", "muted"))
    lines.append(("SIDE EFFECTS", "section"))
    lines.extend((effect_text(item), "body") for item in module.get("effects", []))
    if not module.get("effects"):
        lines.append(("-", "muted"))
    lines.append(("RELATIONSHIPS", "section"))
    callers = module.get("callers", [])
    callees = module.get("callees", [])
    lines.append(("callers: " + (", ".join(callers) if callers else "-"), "body"))
    lines.append(("callees: " + (", ".join(callees) if callees else "-"), "body"))
    lines.append(("DECOMPOSITION", "section"))
    if outgoing:
        for call in sorted(outgoing, key=lambda item: (int(item.get("order", 0)), str(item.get("callee")))):
            lines.append((f"{call.get('order', '?')}. {call.get('callee')}", "body"))
    else:
        lines.append(("Leaf module: its declared responsibility completes the task.", "muted"))
    card = Card(str(module["id"]), str(module["kind"]), str(module["owner_requirement"]), str(module["display_name"]), lines, layer)
    card.measure()
    return card


def contract_card(contract: dict[str, Any]) -> Card:
    req = str(contract.get("requirement_id") or "UNKNOWN")
    lines: list[tuple[str, str]] = [(str(contract.get("spec") or "-"), "body")]
    lines.append(("INPUTS", "section"))
    lines.extend((field_text(item), "body") for item in contract.get("inputs", []))
    lines.append(("OUTPUTS", "section"))
    lines.extend((field_text(item), "body") for item in contract.get("outputs", []))
    lines.append(("REQUIRED SIDE EFFECTS", "section"))
    lines.extend((effect_text(item), "body") for item in contract.get("effects", []))
    card = Card(f"contract:{req}", "CONTRACT", req, f"Requirement Contract · {req}", lines, 0)
    card.measure()
    return card


def compute_layers(modules: dict[str, dict[str, Any]], calls: list[dict[str, Any]]) -> dict[str, int]:
    incoming: dict[str, list[str]] = {key: [] for key in modules}
    for call in calls:
        incoming[str(call["callee"])].append(str(call["caller"]))
    memo: dict[str, int] = {}
    def depth(module_id: str, active: set[str]) -> int:
        if module_id in memo:
            return memo[module_id]
        if module_id in active:
            return {"API": 1, "FUNC": 2, "DB": 3}.get(str(modules[module_id]["kind"]), 2)
        parents = incoming.get(module_id, [])
        same_owner = [p for p in parents if modules[p]["owner_requirement"] == modules[module_id]["owner_requirement"]]
        if not same_owner:
            value = {"API": 1, "FUNC": 2, "DB": 3}.get(str(modules[module_id]["kind"]), 2)
        else:
            value = max(depth(parent, active | {module_id}) + 1 for parent in same_owner)
        memo[module_id] = value
        return value
    for module_id in modules:
        depth(module_id, set())
    return memo


def edge_label(call: dict[str, Any]) -> str:
    inputs = [str(item) for item in call.get("inputs", [])]
    outputs = [str(item) for item in call.get("outputs", [])]
    parts = [f"call #{call.get('order', '?')}"]
    if inputs:
        parts.append("in: " + ", ".join(inputs))
    if outputs:
        parts.append("out: " + ", ".join(outputs))
    return " | ".join(parts)


def layout(contracts: list[dict[str, Any]], modules: dict[str, dict[str, Any]], calls: list[dict[str, Any]]) -> tuple[dict[str, Card], float, float, list[tuple[str, float, float]]]:
    outgoing: dict[str, list[dict[str, Any]]] = {key: [] for key in modules}
    for call in calls:
        outgoing[str(call["caller"])].append(call)
    layers = compute_layers(modules, calls)
    contract_map = {str(item.get("requirement_id")): item for item in contracts}
    reqs = sorted(set(contract_map) | {str(item["owner_requirement"]) for item in modules.values()})
    cards: dict[str, Card] = {}
    sections: list[tuple[str, float, float]] = []
    y = 140.0
    max_width = 1000.0
    for req in reqs:
        contract = contract_card(contract_map.get(req, {"requirement_id": req, "spec": "No requirement contract."}))
        cards[contract.id] = contract
        req_cards = [contract]
        for module_id, module in modules.items():
            if module["owner_requirement"] == req:
                card = module_card(module, outgoing[module_id], layers[module_id])
                cards[module_id] = card
                req_cards.append(card)
        by_layer: dict[int, list[Card]] = {}
        for card in req_cards:
            by_layer.setdefault(card.layer, []).append(card)
        section_width = max(len(values) * CARD_WIDTH + max(0, len(values)-1) * CARD_GAP for values in by_layer.values())
        max_width = max(max_width, section_width + MARGIN * 2)
        section_start = y
        current_y = y + 58
        for layer in sorted(by_layer):
            row = sorted(by_layer[layer], key=lambda item: (item.kind, item.title))
            row_width = len(row) * CARD_WIDTH + max(0, len(row)-1) * CARD_GAP
            start_x = MARGIN + max(0, (section_width - row_width) / 2)
            row_height = max(item.height for item in row)
            for index, card in enumerate(row):
                card.x = start_x + index * (CARD_WIDTH + CARD_GAP)
                card.y = current_y
            current_y += row_height + LAYER_GAP
        section_end = current_y - LAYER_GAP + 44
        sections.append((req, section_start, section_end))
        y = section_end + SECTION_GAP
    return cards, max_width, y, sections


def render(cards: dict[str, Card], calls: list[dict[str, Any]], width: float, height: float, sections: list[tuple[str,float,float]], source: Path) -> str:
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{math.ceil(width)}" height="{math.ceil(height)}" viewBox="0 0 {math.ceil(width)} {math.ceil(height)}">',
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto"><path d="M0 0 L10 5 L0 10 z" fill="#3973B9"/></marker><filter id="shadow" x="-10%" y="-10%" width="120%" height="130%"><feDropShadow dx="0" dy="3" stdDeviation="4" flood-color="#17202A" flood-opacity="0.13"/></filter></defs>',
        '<rect width="100%" height="100%" fill="#FAFBFC"/>',
        '<text x="56" y="52" font-family="Segoe UI,Arial" font-size="30" font-weight="700" fill="#20262E">Stage 2 Design Call Graph</text>',
        f'<text x="56" y="80" font-family="Segoe UI,Arial" font-size="13" fill="#66717D">Source: {escape(str(source))}</text>',
    ]
    for req, start, end in sections:
        parts.append(f'<rect x="20" y="{start:.1f}" width="{width-40:.1f}" height="{end-start:.1f}" rx="18" fill="#FFFFFF" stroke="#D7DCE2"/>')
        parts.append(f'<text x="44" y="{start+34:.1f}" font-family="Segoe UI,Arial" font-size="21" font-weight="700" fill="#20262E">{escape(req)}</text>')
    for index, call in enumerate(calls):
        source = cards.get(str(call.get("caller")))
        target = cards.get(str(call.get("callee")))
        if not source or not target:
            continue
        sx, sy = source.x + source.width/2, source.y + source.height
        tx, ty = target.x + target.width/2, target.y
        mid = sy + max(50, (ty-sy)/2) if ty > sy else max(sy,ty) + 65 + index*4
        parts.append(f'<path d="M {sx:.1f} {sy:.1f} V {mid:.1f} H {tx:.1f} V {ty:.1f}" fill="none" stroke="#3973B9" stroke-width="2.2" marker-end="url(#arrow)"/>')
        label_lines = wrap(edge_label(call), 72)
        lw = min(500, max(190, max(len(line) for line in label_lines)*6.8+20))
        lh = len(label_lines)*16+10
        lx, ly = (sx+tx)/2-lw/2, mid-lh/2
        parts.append(f'<rect x="{lx:.1f}" y="{ly:.1f}" width="{lw:.1f}" height="{lh:.1f}" rx="6" fill="#FFFFFF" stroke="#D7DCE2"/>')
        for li,line in enumerate(label_lines):
            parts.append(f'<text x="{lx+10:.1f}" y="{ly+17+li*16:.1f}" font-family="Consolas,monospace" font-size="10.5" fill="#39434E">{escape(line)}</text>')
    for card in sorted(cards.values(), key=lambda item:(item.y,item.x)):
        fill, stroke, ink = COLORS[card.kind]
        parts.append(f'<g><rect x="{card.x:.1f}" y="{card.y:.1f}" width="{card.width:.1f}" height="{card.height:.1f}" rx="13" fill="#FFFFFF" stroke="{stroke}" stroke-width="2" filter="url(#shadow)"/>')
        parts.append(f'<rect x="{card.x:.1f}" y="{card.y:.1f}" width="{card.width:.1f}" height="42" rx="13" fill="{fill}"/>')
        parts.append(f'<text x="{card.x+16:.1f}" y="{card.y+27:.1f}" font-family="Segoe UI,Arial" font-size="15" font-weight="700" fill="{ink}">{escape(card.kind + " · " + card.title)}</text>')
        cy = card.y + 57
        for text, role in card.lines:
            if role == "section":
                cy += 6
                parts.append(f'<line x1="{card.x+16:.1f}" y1="{cy:.1f}" x2="{card.x+card.width-16:.1f}" y2="{cy:.1f}" stroke="#E1E5EA"/>')
                cy += 16
                color,size,weight,family = stroke,11,700,"Segoe UI,Arial"
            elif role == "muted":
                color,size,weight,family = "#7A838D",10.5,400,"Consolas,monospace"
            else:
                color,size,weight,family = "#28313A",11,400,"Consolas,monospace"
            for line in wrap(text, 66):
                parts.append(f'<text x="{card.x+16:.1f}" y="{cy:.1f}" font-family="{family}" font-size="{size}" font-weight="{weight}" fill="{color}">{escape(line)}</text>')
                cy += LINE_HEIGHT
        parts.append('</g>')
    parts.append('</svg>')
    return "\n".join(parts) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Render compact Stage 2 design artifacts as an SVG call graph.")
    parser.add_argument("input", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args()
    design_dir = resolve_design_dir(args.input)
    contracts, modules, calls = load(design_dir)
    cards, width, height, sections = layout(contracts, modules, calls)
    output = (args.output or design_dir.parent.parent / "visualizations" / "design-call-graph.svg").resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(cards, calls, width, height, sections, design_dir), encoding="utf-8")
    print(f"Wrote {output} ({len(cards)} cards, {len(calls)} edges, {math.ceil(width)}x{math.ceil(height)} SVG units)")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
