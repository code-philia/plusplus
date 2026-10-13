from __future__ import annotations

import hashlib
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
from model.contracts import StructuredModel
from model.structured import BaseStructuredAgent
from utils.jsonio import append_jsonl, write_json_atomic
from .runtime import HTML_TAG, RUNTIME_PATH, normalize_environment, render_runtime_source


SEED_PATH = "backend/src/validation/seed.ts"
START_PATH = "backend/src/validation/start.ts"
MARKER = ".arc/validation/preparation.json"
START_COMMAND = "npm run build -w @arc/shared && npm run build && node ./dist/validation/start.js"


def check_validation_readiness(root: Path) -> None:
    """Check deployable artifacts without using or seeding the application database."""
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
            process = subprocess.Popen(['node', 'dist/server.js'], cwd=root / 'backend',
                                       env=environment, stdout=output, stderr=subprocess.STDOUT)
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
OUTPUT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "seed_source": {"type": "string"},
        "request_files": {"type": "array", "items": {"type": "string"}},
    }, "required": ["seed_source", "request_files"],
}
INSTRUCTIONS = """Generate database seed code for external validation AFTER application implementation.
This is not internal TDD and you cannot see or modify external validation tests.
Use seed_data only: instantiate every lifecycle SEED record and no CREATED/DERIVED records.
Requirement data.requires is only an association, not additional rows to insert.
The final generated schema, DDL and current database/business source are authoritative for physical
table/column names, foreign keys, enum representation and credentials/password hashing.
Do not hardcode example-specific behavior into the application or change any existing source.
Return a complete TypeScript module for backend/src/validation/seed.ts exporting:
import type { DatabaseClient } from "../db/client.js";
export async function seedValidation(client: DatabaseClient): Promise<void> { ... }
Use client.connection.prepare(sql).run(...) for parameterized insert statements. Match the REAL
application's password encoding and database representations, using existing pure helpers when safe.
If a helper needs a bound database, bind the supplied client explicitly. Never invoke the server,
HTTP APIs or UI, create a second connection, close the supplied client or use another database.
The startup wrapper initializes tables, begins a transaction, awaits this function, records a seed
fingerprint and commits. Do not BEGIN/COMMIT or use schemaTransaction inside this function.
Insert parents before children; do not disable foreign keys, drop tables or delete existing data.
Do not swallow insertion errors or skip missing required fixture properties. A clean validation
database is used initially. Fail visibly on incompatible existing rows instead of overwriting them.
Do not seed data on module import: all side effects belong inside seedValidation.
No new dependencies, tests or README. The output must compile with the application's strict TS settings.
source_files contains real complete source. file_catalog contains other discoverable application
files; request missing files with request_files and seed_source:"" before generating code.
When returning the completed source use request_files:[]. Compiler build errors are supplied on retries.
"""


def _start_source(fingerprint: str, environment: dict[str, Any]) -> str:
    return '''// Optional validation startup. Runs only through the configured application start command.
import { fileURLToPath } from "node:url";
import { createDatabaseClient } from "../db/client.js";
import { initializeDatabase } from "../db/initialize.js";
import { installValidationRuntime } from "./runtime.js";

process.env.DATABASE_URL ??= fileURLToPath(new URL("../../data/validation.db", import.meta.url));
TIMEZONE
const environment = ENVIRONMENT as const;
const fingerprint = FINGERPRINT;
const client = createDatabaseClient();
try {
  initializeDatabase(client.connection);
  client.connection.exec('CREATE TABLE IF NOT EXISTS "__arc_validation_seed" (fingerprint TEXT PRIMARY KEY)');
  client.connection.exec("BEGIN IMMEDIATE");
  try {
    installValidationRuntime(client.connection, environment);
    const applied = client.connection.prepare('SELECT fingerprint FROM "__arc_validation_seed" LIMIT 1').get() as { fingerprint: string } | undefined;
    if (applied && applied.fingerprint !== fingerprint) {
      throw new Error("Validation fixture definition changed; choose a fresh DATABASE_URL.");
    }
    if (!applied) {
      const { seedValidation } = await import("./seed.js");
      await seedValidation(client);
      client.connection.prepare('INSERT INTO "__arc_validation_seed" (fingerprint) VALUES (?)').run(fingerprint);
    }
    client.connection.exec("COMMIT");
  } catch (error) {
    client.connection.exec("ROLLBACK");
    throw error;
  }
} finally {
  client.close();
}
// Dynamic import ensures seeding finishes before any server module executes.
await import("../server.js");
'''.replace("FINGERPRINT", json.dumps(fingerprint)).replace("ENVIRONMENT", json.dumps(environment, ensure_ascii=False)).replace(
        "TIMEZONE", "process.env.TZ = " + json.dumps(environment["timezone"]) + ";" if environment.get("timezone") else "")


def disable_validation_startup(root: Path) -> None:
    """Detach an earlier opt-in hook without deleting any persisted database."""
    marker = root / MARKER
    if not marker.is_file():
        return
    state = json.loads(marker.read_text(encoding="utf-8"))
    backend = root / "backend/package.json"
    package = json.loads(backend.read_text(encoding="utf-8"))
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
    def __init__(self, model: StructuredModel, root: Path) -> None:
        self.root = root.expanduser().resolve()
        self.agent = BaseStructuredAgent(model, schema_name="arc_validation_seed",
            instructions=INSTRUCTIONS, output_schema=OUTPUT_SCHEMA, retries=2,
            model_log=lambda row: append_jsonl(self.root / ".arc/validation/model_calls.jsonl", row),
            agent_name="ValidationPreparationPass")

    def generate(self, requirement_ir: dict[str, Any]) -> dict[str, str]:
        if not isinstance(requirement_ir.get("data", []), list) or not isinstance(requirement_ir.get("environment", {}), dict):
            raise ValueError("ROOT.data must be a list and ROOT.environment must be an object")
        if any(not isinstance(row, dict) for row in requirement_ir.get("data", [])):
            raise ValueError("Each ROOT.data record must be an object")
        seed_data = [row for row in requirement_ir.get("data", []) if row.get("lifecycle") == "SEED"]
        environment = normalize_environment(requirement_ir.get("environment", {}))
        schema_path = self.root / ".arc/design/database/schema.json"
        if not schema_path.is_file() or not (self.root / "backend/src/server.ts").is_file():
            raise ValueError("Validation preparation requires an implemented, initialized application")
        backend_package = self.root / "backend/package.json"
        package = json.loads(backend_package.read_text(encoding="utf-8"))
        if "better-sqlite3" not in package.get("dependencies", {}):
            raise ValueError("Validation seed preparation currently supports the SQLite application profile")
        old_state_path = self.root / MARKER
        previous = json.loads(old_state_path.read_text(encoding="utf-8")) if old_state_path.is_file() else {}
        original_start = package.get("scripts", {}).get("start")
        if original_start == START_COMMAND:
            original_start = previous.get("original_backend_start")
        if not isinstance(original_start, str) or not original_start:
            raise ValueError("Backend package must define its application start command")
        if "node ./dist/server.js" not in original_start and "node dist/server.js" not in original_start:
            raise ValueError("Unsupported start command: validation startup requires backend/dist/server.js")
        catalog: dict[str, Path] = {}
        for directory in ("backend/src", "shared/src", "backend/sql"):
            for path in (self.root / directory).rglob("*"):
                if path.is_file() and self.root in path.resolve().parents and path.suffix in {".ts", ".sql", ".json"}:
                    relative = path.relative_to(self.root).as_posix()
                    if relative not in {SEED_PATH, START_PATH, RUNTIME_PATH}:
                        catalog[relative] = path
        loaded = {path for path in catalog if path.startswith(("backend/src/db/", "backend/sql/"))}
        loaded.add("backend/src/server.ts")
        for path in ("backend/src/app.ts",):
            if path in catalog:
                loaded.add(path)
        fingerprint = hashlib.sha256(json.dumps({"data": seed_data, "environment": environment,
            "schema": json.loads(schema_path.read_text(encoding="utf-8"))}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        seed_fingerprint = fingerprint
        originals = {path: (self.root / path).read_bytes() if (self.root / path).is_file() else None
                     for path in (SEED_PATH, START_PATH, RUNTIME_PATH, "backend/package.json", "package.json",
                                  "frontend/index.html", "frontend/dist/index.html", MARKER, ".arc/validation/environment.json")}
        self.root.joinpath(SEED_PATH).parent.mkdir(parents=True, exist_ok=True)
        requests: list[str] = []

        def validate(output: dict[str, Any]) -> list[str]:
            nonlocal requests, seed_fingerprint
            if not isinstance(output, dict) or set(output) != {"seed_source", "request_files"}:
                return ["Return only seed_source and request_files"]
            source, wanted = output["seed_source"], output["request_files"]
            if not isinstance(source, str) or not isinstance(wanted, list) or any(not isinstance(path, str) for path in wanted):
                return ["seed_source must be text; request_files must be a list of paths"]
            if wanted:
                if source or any(path not in catalog or path in loaded for path in wanted):
                    return ["Request unloaded catalog files with an empty seed_source"]
                requests = wanted
                return []
            if "seedValidation" not in source or not source.strip():
                return ["Export seedValidation(client: DatabaseClient): Promise<void>"]
            (self.root / SEED_PATH).write_text(source, encoding="utf-8")
            seed_fingerprint = hashlib.sha256((fingerprint + source).encode()).hexdigest()
            (self.root / START_PATH).write_text(_start_source(seed_fingerprint, environment), encoding="utf-8")
            (self.root / RUNTIME_PATH).write_text(render_runtime_source(), encoding="utf-8")
            html_path = self.root / "frontend/index.html"
            html = html_path.read_text(encoding="utf-8")
            if HTML_TAG not in html:
                if "<head>" not in html:
                    return ["Frontend HTML must contain a head element for synchronous validation bootstrap"]
                html_path.write_text(html.replace("<head>", "<head>\n    " + HTML_TAG, 1), encoding="utf-8")
            build = ProjectBuilder(self.root).build()
            if not build.ok:
                return build.errors
            requests = []
            return []

        try:
            for expansion in range(6):
                context = {"seed_data": seed_data, "database_schema": json.loads(schema_path.read_text(encoding="utf-8")),
                    "source_files": {path: catalog[path].read_text(encoding="utf-8") for path in sorted(loaded)},
                    "file_catalog": sorted(catalog),
                    "package": package, "tsconfig": json.loads((self.root / "backend/tsconfig.json").read_text(encoding="utf-8"))}
                if len(json.dumps(context, ensure_ascii=False)) > 1_000_000:
                    raise ValueError("Validation source context exceeds one million characters")
                result = self.agent.invoke(context, validate=validate)
                if not result.ok:
                    raise ValueError("Validation seed generation failed: " + "; ".join(result.errors))
                if requests:
                    if expansion == 5:
                        raise ValueError("Validation context expansion limit exceeded")
                    loaded.update(requests)
                    requests = []
                    continue
                break
            package["scripts"]["start"] = START_COMMAND
            write_json_atomic(backend_package, package)
            root_package_path = self.root / "package.json"
            root_package = json.loads(root_package_path.read_text(encoding="utf-8"))
            root_package.setdefault("scripts", {}).setdefault("start", "npm run build -w @arc/frontend && npm run start -w @arc/backend")
            write_json_atomic(root_package_path, root_package)
            state = {"enabled": True, "original_backend_start": original_start,
                     "fingerprint": seed_fingerprint, "seed_ids": [row["id"] for row in seed_data],
                     "startup": START_PATH, "seed": SEED_PATH, "runtime": RUNTIME_PATH, "executed": False}
            write_json_atomic(self.root / MARKER, state)
            write_json_atomic(self.root / ".arc/validation/environment.json", environment)
        except Exception:
            for path, content in originals.items():
                target = self.root / path
                if content is None:
                    target.unlink(missing_ok=True)
                else:
                    target.write_bytes(content)
            raise
        return {"validation_preparation": str(self.root / MARKER), "validation_seed": str(self.root / SEED_PATH),
                "validation_startup": str(self.root / START_PATH), "validation_runtime": str(self.root / RUNTIME_PATH)}
