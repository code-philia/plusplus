from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

from compiler.build.stage import ProjectBuilder
from utils.encoding import utf8_environment
from utils.jsonio import write_json_atomic
from .runtime import HTML_TAG, RUNTIME_PATH, normalize_environment, render_runtime_source


SEED_PATH = "backend/src/validation/seed.ts"
START_PATH = "backend/src/validation/start.ts"
MARKER = ".arc/validation/preparation.json"
START_COMMAND = "npm run build && node ./dist/validation/start.js"


def check_validation_readiness(root: Path, *, validation: bool = False) -> None:
    """Check deployable artifacts using an isolated temporary database."""
    root = root.expanduser().resolve()
    build = ProjectBuilder(root).build()
    if not build.ok:
        raise ValueError('Validation preparation readiness build failed: ' + '; '.join(build.errors))
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='arc-validation-readiness-') as directory:
        environment = {**os.environ, 'PORT': str(port), 'NODE_ENV': 'test',
                       'DATABASE_URL': str(Path(directory) / 'probe.db')}
        with tempfile.TemporaryFile() as output:
            process = subprocess.Popen(['node', 'dist/validation/start.js' if validation else 'dist/server.js'], cwd=root / 'backend',
                                       env=utf8_environment(environment), stdout=output, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 30
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                while process.poll() is None and time.monotonic() < deadline:
                    try:
                        with opener.open(f'http://127.0.0.1:{port}/__arc/health', timeout=1) as response:
                            if response.status == 200 and process.poll() is None:
                                return
                    except (OSError, urllib.error.URLError):
                        pass
                    time.sleep(0.2)
                output.seek(0)
                detail = output.read().decode('utf-8', errors='replace')[-4000:]
                raise ValueError('Validation preparation readiness startup failed: ' + detail)
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
def _start_source(environment: dict[str, Any]) -> str:
    return '''// Apply validation clock/timezone before the ordinary application startup.
import { installValidationRuntime } from "./runtime.js";
TIMEZONE
const environment = ENVIRONMENT as const;
installValidationRuntime(environment);
// Ordinary startup owns database reset and the shared cumulative seed.
process.env.ARC_VALIDATION_PREPARED = "0";
await import("../server.js");
'''.replace("ENVIRONMENT", json.dumps(environment, ensure_ascii=False)).replace(
        "TIMEZONE", "process.env.TZ = " + json.dumps(environment["timezone"]) + ";" if environment.get("timezone") else "")


def disable_validation_startup(root: Path) -> None:
    """Detach an earlier opt-in hook without deleting any persisted database."""
    marker = root / MARKER
    if not marker.is_file():
        return
    state = json.loads(marker.read_text(encoding="utf-8-sig"))
    backend = root / "backend/package.json"
    package = json.loads(backend.read_text(encoding="utf-8-sig"))
    if package.get("scripts", {}).get("start") == START_COMMAND:
        package["scripts"]["start"] = state["original_backend_start"]
        write_json_atomic(backend, package)
    for relative in ("frontend/index.html", "frontend/dist/index.html"):
        html = root / relative
        if html.is_file():
            content = html.read_text(encoding="utf-8")
            if HTML_TAG in content:
                html.write_text(content.replace(HTML_TAG, ""), encoding="utf-8")
    state["enabled"] = False
    write_json_atomic(marker, state)


class ValidationPreparationPass:
    """Deterministically configure validation time without generating or inserting data."""

    def __init__(self, model, root: Path) -> None:
        self.root = root.expanduser().resolve()

    def generate(self, requirement_ir: dict[str, Any]) -> dict[str, str]:
        if not isinstance(requirement_ir.get("environment", {}), dict):
            raise ValueError("ROOT.environment must be an object")
        environment = normalize_environment(requirement_ir.get("environment", {}))
        if not (self.root / "backend/src/server.ts").is_file():
            raise ValueError("Validation preparation requires an initialized application")
        backend_package = self.root / "backend/package.json"
        package = json.loads(backend_package.read_text(encoding="utf-8-sig"))
        marker = self.root / MARKER
        previous = json.loads(marker.read_text(encoding="utf-8-sig")) if marker.is_file() else {}
        original_start = package.get("scripts", {}).get("start")
        if original_start == START_COMMAND:
            original_start = previous.get("original_backend_start")
        if not isinstance(original_start, str) or not original_start:
            raise ValueError("Backend package must define its application start command")
        if "node ./dist/server.js" not in original_start and "node dist/server.js" not in original_start:
            raise ValueError("Validation startup requires backend/dist/server.js")
        paths = (START_PATH, RUNTIME_PATH, SEED_PATH, "backend/package.json", "frontend/index.html",
                 MARKER, ".arc/validation/environment.json")
        originals = {path: (self.root / path).read_bytes() if (self.root / path).is_file() else None
                     for path in paths}
        try:
            (self.root / START_PATH).parent.mkdir(parents=True, exist_ok=True)
            (self.root / START_PATH).write_text(_start_source(environment), encoding="utf-8")
            (self.root / RUNTIME_PATH).write_text(render_runtime_source(), encoding="utf-8")
            # Retire only a seed explicitly owned by the previous preparation stage.
            if previous.get("seed") == SEED_PATH:
                (self.root / SEED_PATH).unlink(missing_ok=True)
            html_path = self.root / "frontend/index.html"
            html = html_path.read_text(encoding="utf-8")
            if HTML_TAG not in html:
                if "<head>" not in html:
                    raise ValueError("Frontend HTML must contain a head element")
                html_path.write_text(html.replace("<head>", "<head>\n    " + HTML_TAG, 1), encoding="utf-8")
            build = ProjectBuilder(self.root).build()
            if not build.ok:
                raise ValueError("Validation environment build failed: " + "; ".join(build.errors))
            package["scripts"]["start"] = START_COMMAND
            write_json_atomic(backend_package, package)
            write_json_atomic(marker, {"enabled": True, "original_backend_start": original_start,
                "startup": START_PATH, "runtime": RUNTIME_PATH, "environment_only": True,
                "database_startup": "ordinary reset and shared seed"})
            write_json_atomic(self.root / ".arc/validation/environment.json", environment)
        except Exception:
            for path, content in originals.items():
                target = self.root / path
                if content is None:
                    target.unlink(missing_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)
            raise
        return {"validation_preparation": str(marker),
                "validation_startup": str(self.root / START_PATH),
                "validation_runtime": str(self.root / RUNTIME_PATH)}
