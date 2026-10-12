"""Render each model request/reply as a page in a standalone HTML file."""
from __future__ import annotations

import argparse
import json
import re
from html import escape
from pathlib import Path
from typing import Any


MARKER = re.compile(r"^\[([^\n]+?)\] \[([^\n]+?)\] (MODEL_[A-Z_]+)([^\n]*)$", re.MULTILINE)


def requirement_ids(text: str) -> list[str]:
    """Use task metadata, never IDs from reusable context or the model reply."""
    task = re.search(r"^## Current task\s*\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    region = task.group(1) if task else text
    ids: list[str] = []
    for block in re.findall(r"```json\s*\n(.*?)\n```", region, re.DOTALL):
        try:
            payload = json.loads(block)
        except ValueError:
            continue
        if not isinstance(payload, dict):
            continue
        requirement = payload.get("requirement", {})
        candidates = [payload.get("requirement_id")]
        if isinstance(requirement, dict):
            candidates.append(requirement.get("id", requirement.get("requirement_id")))
        candidates.extend(payload.get("requirement_ids", []))
        focus = payload.get("focus_entity", {})
        if isinstance(focus, dict):
            candidates.extend(focus.get("requirement_ids", []))
        ids.extend(value for value in candidates if isinstance(value, str))
    return list(dict.fromkeys(ids))


def render_json(value: Any, label: str = "output", depth: int = 0) -> str:
    if isinstance(value, (dict, list)):
        entries = value.items() if isinstance(value, dict) else enumerate(value)
        children = "".join(render_json(item, str(key), depth + 1) for key, item in entries)
        kind = "object" if isinstance(value, dict) else "array"
        return (f'<details class="tree" open><summary>'
                f'{escape(label)} <span>{kind} · {len(value)}</span></summary>{children}</details>')
    literal = json.dumps(value, ensure_ascii=False)
    return f'<div class="leaf"><b>{escape(label)}</b><code>{escape(literal)}</code></div>'


def read_log(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    markers = list(MARKER.finditer(text))
    sections: dict[str, str] = {}
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        sections[marker.group(3)] = text[marker.end():end].strip()
        if marker.group(3) in {"MODEL_ERROR", "MODEL_CALL_ERROR"}:
            sections[marker.group(3)] = (marker.group(4).strip() + "\n" + sections[marker.group(3)]).strip()
    first = markers[0] if markers else None
    raw = sections.get("MODEL_OUTPUT", "")
    cleaned = re.sub(r"^```(?:json)?\s*\n|\n```\s*$", "", raw).strip()
    try:
        output = render_json(json.loads(cleaned))
        output += f'<details><summary>格式化 JSON</summary><pre>{escape(json.dumps(json.loads(cleaned), ensure_ascii=False, indent=2))}</pre></details>'
    except ValueError:
        output = '<p class="notice">没有 JSON 输出，或输出不是有效 JSON。</p>' + f'<pre>{escape(raw)}</pre>'
    return {"file": path.name, "time": first.group(1) if first else "",
            "stage": first.group(2) if first else "unknown",
            "meta": first.group(4).strip() if first else "",
            "reqs": requirement_ids(sections.get("MODEL_INPUT", "")),
            "output": output, "error": sections.get("MODEL_ERROR", "") or sections.get("MODEL_CALL_ERROR", ""),
            "reasoning": sections.get("MODEL_REASONING", "")}


def render(arc: Path, rows: list[dict[str, Any]]) -> str:
    pages, options = [], []
    for index, row in enumerate(rows):
        reqs = ", ".join(row["reqs"]) or "未指定 REQ"
        options.append(f'<button class="entry" data-index="{index}">'
                       f'<span class="entry-number">{index + 1:02d}</span><span class="entry-text">'
                       f'<strong>{escape(row["stage"])}</strong><small>{escape(reqs)}</small>'
                       f'</span>{"<span class=error-dot>●</span>" if row["error"] else ""}</button>')
        error = f'<pre class="notice">{escape(row["error"])}</pre>' if row["error"] else ""
        reasoning = (f'<details><summary>模型推理</summary><pre>{escape(row["reasoning"])}</pre></details>'
                     if row["reasoning"] else "")
        pages.append(f'<article id="call-{index + 1}" {"hidden" if index else ""}>'
                     f'<h1>{escape(row["stage"])}</h1><p class="req">REQ：{escape(reqs)}</p>'
                     f'<p class="meta">{escape(row["time"])} · {escape(row["meta"])}</p>'
                     f'<p class="meta">{escape(row["file"])}</p>{error}'
                     f'<h2>模型 JSON 输出</h2>{row["output"]}{reasoning}</article>')
    template = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>ARC 模型交互</title><style>
*{box-sizing:border-box}body{margin:0;background:#f3f5fa;color:#27364b;font:14px/1.65 system-ui,sans-serif}
aside{position:fixed;inset:0 auto 0 0;width:310px;background:#fff;border-right:1px solid #e2e7ef;display:flex;flex-direction:column}
.brand{padding:24px 22px 16px;font-size:20px;font-weight:750;letter-spacing:-.5px}.brand small{display:block;color:#8390a3;font-size:11px;letter-spacing:1.5px;font-weight:500}
.pager{display:flex;align-items:center;gap:8px;padding:0 18px 18px;border-bottom:1px solid #edf0f5}
button{font:inherit;border:1px solid #dce2ec;border-radius:8px;background:white;color:#354560;cursor:pointer}.pager button{padding:6px 10px}.pager button:hover:not(:disabled){background:#eff4ff;border-color:#9eb7ef}button:disabled{opacity:.4;cursor:default}#counter{margin-left:auto;color:#8793a6;font-size:12px;white-space:nowrap}
nav{overflow:auto;flex:1;padding:12px}.entry{display:flex;align-items:center;gap:10px;width:100%;text-align:left;border:1px solid transparent;padding:10px;margin:3px 0;background:transparent}.entry:hover{background:#f5f7fb}.entry.active{background:#edf3ff;border-color:#d7e4ff;color:#285cb6}.entry-number{color:#8d9bb1;font:12px ui-monospace,monospace}.entry-text{min-width:0;flex:1}.entry strong{display:block;font-size:12px;font-weight:600;overflow-wrap:anywhere}.entry small{display:block;color:#8290a6;font-size:11px;margin-top:3px}.error-dot{color:#ca5353}
main{margin-left:310px;padding:24px 36px;max-width:1650px}article{background:white;padding:32px;border:1px solid #e5eaf2;border-radius:16px;box-shadow:0 8px 30px #22365506}h1{margin:0;font-size:26px;letter-spacing:-.6px;overflow-wrap:anywhere}h2{font-size:15px;margin-top:26px;padding-top:22px;border-top:1px solid #edf0f5}
.meta{color:#8190a5;font-size:12px;overflow-wrap:anywhere}.req{display:inline-block;background:#eef4ff;color:#3562a6;font-size:12px;font-weight:600;padding:4px 12px;border-radius:20px}.tree{margin:8px 0 8px 12px;border-left:1px solid #dce6f2;padding-left:14px}
summary{cursor:pointer;font-weight:600;overflow-wrap:anywhere;padding:3px 0}summary::marker{color:#8fa8ca}summary span{color:#96a2b5;font-weight:400;font-size:11px;margin-left:8px}
.leaf{display:flex;gap:18px;margin:6px 0 6px 24px;align-items:baseline;padding:4px 8px;border-radius:5px}.leaf:hover{background:#f6f8fc}.leaf b{min-width:110px;font:12px ui-monospace,monospace;color:#546784;overflow-wrap:anywhere}code{white-space:pre-wrap;overflow-wrap:anywhere;color:#20796c;min-width:0;font:12px/1.8 ui-monospace,monospace}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f7fb;border:1px solid #e9edf4;border-radius:10px;padding:18px;font:12px/1.8 ui-monospace,monospace}.notice{color:#b34040}details>pre{max-height:70vh;overflow:auto}
@media(max-width:850px){aside{width:235px}main{margin-left:235px;padding:18px}article{padding:20px}.leaf{display:block;margin-left:8px}.leaf b{display:block}}
@media(max-width:580px){aside{position:relative;width:100%;height:260px}.brand{padding:12px 18px}.pager{padding-bottom:10px}main{margin:0;padding:12px}h1{font-size:21px}}
@media print{aside{display:none}main{margin:0;padding:0}body{background:white}article[hidden]{display:block}article{break-after:page;box-shadow:none}details>pre{max-height:none}}
</style></head><body><aside><div class="brand">ARC 模型交互<small>MODEL INTERACTIONS</small></div><div class="pager"><button id="prev">← 上一个</button><button id="next">下一个 →</button><span id="counter"></span></div><nav aria-label="模型交互列表">OPTIONS</nav></aside>
<main><p class="meta">SOURCE</p>PAGES</main><script>
const pages=[...document.querySelectorAll('article')],entries=[...document.querySelectorAll('.entry')];
let current=0;
function show(index){current=Math.max(0,Math.min(pages.length-1,index));pages.forEach((page,i)=>page.hidden=i!==current);
entries.forEach((entry,i)=>{entry.classList.toggle('active',i===current);if(i===current)entry.setAttribute('aria-current','page');else entry.removeAttribute('aria-current')});
const entry=entries[current],nav=entry.parentElement;if(entry.offsetTop<nav.scrollTop||entry.offsetTop+entry.offsetHeight>nav.scrollTop+nav.clientHeight)nav.scrollTop=entry.offsetTop-nav.offsetTop-nav.clientHeight/2;
document.querySelector('#counter').textContent=(current+1)+' / '+pages.length;
document.querySelector('#prev').disabled=current===0;document.querySelector('#next').disabled=current===pages.length-1;
history.replaceState(null,'','#call-'+(current+1));window.scrollTo(0,0)}
document.querySelector('#prev').onclick=()=>show(current-1);document.querySelector('#next').onclick=()=>show(current+1);entries.forEach(entry=>entry.onclick=()=>show(Number(entry.dataset.index)));
document.addEventListener('keydown',event=>{if(event.key==='ArrowLeft')show(current-1);if(event.key==='ArrowRight')show(current+1)});
show((Number(location.hash.replace('#call-',''))||1)-1);
</script></body></html>"""
    return template.replace("OPTIONS", "".join(options)).replace("SOURCE", escape(str(arc))).replace("PAGES", "".join(pages))


def main() -> None:
    parser = argparse.ArgumentParser(description="将 .arc/model_logs 导出为每次交互一页的 HTML。")
    parser.add_argument("arc_dir", type=Path, help="运行的 .arc 目录")
    args = parser.parse_args()
    arc = args.arc_dir.expanduser().resolve()
    logs = sorted((arc / "model_logs").glob("*.log"))
    if not logs:
        parser.error(f"没有找到模型日志：{arc / 'model_logs'}")
    rows = sorted((read_log(path) for path in logs), key=lambda row: (row["time"], row["file"]))
    output = arc / "model_interactions.html"
    output.write_text(render(arc, rows), encoding="utf-8")
    print(f"{len(rows)} 次模型交互 → {output}")


if __name__ == "__main__":
    main()
