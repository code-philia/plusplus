from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from arcbench_agent_runtime.jsonio import write_json_atomic


PROJECT_STATUS = "PROJECT_INITIALIZED"
PROJECT_PROFILE_ID = "web-react18-express-drizzle-sqlite"


class ProjectInitializationError(RuntimeError):
    """A deterministic project initialization failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class DependencyCatalog:
    """Compiler-owned, reproducible versions for the generated web workspace."""

    node_major: str = "22.12.0"
    npm_minimum: str = "10.9.0"
    create_vite: str = "7.1.3"
    typescript: str = "5.9.2"
    react: str = "18.3.1"
    react_dom: str = "18.3.1"
    react_router_dom: str = "7.8.2"
    vite: str = "7.1.4"
    vite_react_plugin: str = "5.0.2"
    express: str = "5.1.0"
    zod: str = "4.1.5"
    drizzle_orm: str = "0.44.5"
    drizzle_kit: str = "0.31.4"
    better_sqlite3: str = "12.2.0"
    tsx: str = "4.20.5"
    types_node: str = "22.18.0"
    types_react: str = "18.3.23"
    types_react_dom: str = "18.3.7"
    types_express: str = "5.0.3"
    types_better_sqlite3: str = "7.6.13"

    def to_dict(self) -> dict[str, str]:
        return {
            "node": self.node_major,
            "npm": self.npm_minimum,
            "create-vite": self.create_vite,
            "typescript": self.typescript,
            "react": self.react,
            "react-dom": self.react_dom,
            "react-router-dom": self.react_router_dom,
            "vite": self.vite,
            "@vitejs/plugin-react": self.vite_react_plugin,
            "express": self.express,
            "zod": self.zod,
            "drizzle-orm": self.drizzle_orm,
            "drizzle-kit": self.drizzle_kit,
            "better-sqlite3": self.better_sqlite3,
            "tsx": self.tsx,
            "@types/node": self.types_node,
            "@types/react": self.types_react,
            "@types/react-dom": self.types_react_dom,
            "@types/express": self.types_express,
            "@types/better-sqlite3": self.types_better_sqlite3,
        }


@dataclass(slots=True)
class ProjectInitializationResult:
    ok: bool
    status: str
    artifacts: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


class ProjectInitializer:
    """Materialize the Stage 2.5 workspace without using ARC project templates."""

    _PROJECT_TARGETS = (
        "package.json",
        "package-lock.json",
        "node_modules",
        ".gitignore",
        ".env.example",
        "frontend",
        "backend",
        "shared",
    )

    def __init__(
        self,
        output_root: Path,
        *,
        web_port: int,
        catalog: DependencyCatalog | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self.output_root = output_root.expanduser().resolve()
        self.web_port = int(web_port)
        self.catalog = catalog or DependencyCatalog()
        self.environment = dict(os.environ if environment is None else environment)
        self.arc_root = self.output_root / ".arc"
        self.staging_root = self.arc_root / "staging" / "project-init"
        self.staged_project = self.staging_root / "project"
        self.project_artifact_root = self.arc_root / "project"
        self._promoted_targets: list[Path] = []
        self._project_artifact_owned = False

    def initialize(self, app_type: str) -> ProjectInitializationResult:
        try:
            normalized_type = str(app_type or "web").strip().lower()
            if normalized_type != "web":
                raise ProjectInitializationError(
                    "PROJECT_PROFILE_INVALID",
                    f"Stage 2.5 currently supports app_type=web, received {normalized_type!r}.",
                )
            self._validate_toolchain()
            self._validate_target()
            self._prepare_staging()
            self._run_official_initializers()
            self._normalize_workspace()
            self._create_lockfile()
            self._validate_staged_project()
            self._promote()
            self._install_promoted_workspace()
            artifacts = self._emit_manifests()
            self._cleanup_staging()
            return ProjectInitializationResult(ok=True, status=PROJECT_STATUS, artifacts=artifacts)
        except ProjectInitializationError as exc:
            self._rollback_promoted_targets()
            self._cleanup_staging()
            return ProjectInitializationResult(
                ok=False,
                status="PROJECT_INITIALIZATION_FAILED",
                errors=[str(exc)],
            )
        except OSError as exc:
            self._rollback_promoted_targets()
            self._cleanup_staging()
            return ProjectInitializationResult(
                ok=False,
                status="PROJECT_INITIALIZATION_FAILED",
                errors=[f"PROJECT_SCAFFOLD_FAILED: {exc}"],
            )
        except Exception as exc:  # Keep unexpected initializer faults inside the compiler stage boundary.
            self._rollback_promoted_targets()
            self._cleanup_staging()
            return ProjectInitializationResult(
                ok=False,
                status="PROJECT_INITIALIZATION_FAILED",
                errors=[f"PROJECT_INITIALIZATION_INTERNAL_ERROR: {exc}"],
            )

    def _validate_target(self) -> None:
        self.output_root.mkdir(parents=True, exist_ok=True)
        conflicts = [name for name in self._PROJECT_TARGETS if (self.output_root / name).exists()]
        if self.project_artifact_root.exists():
            conflicts.append(".arc/project")
        if conflicts:
            raise ProjectInitializationError(
                "PROJECT_TARGET_CONFLICT",
                f"Project target already exists: {', '.join(conflicts)}",
            )

    def _validate_toolchain(self) -> None:
        command_root = self.output_root if self.output_root.is_dir() else self.output_root.parent
        node = self._run(["node", "--version"], cwd=command_root)
        npm = self._run(["npm", "--version"], cwd=command_root)
        actual_node = self._parse_version(node.stdout.strip().lstrip("v"))
        required_node = self._parse_version(self.catalog.node_major)
        if actual_node < required_node:
            raise ProjectInitializationError(
                "PROJECT_PROFILE_INVALID",
                f"Node.js {self.catalog.node_major}+ is required, found {node.stdout.strip()!r}.",
            )
        actual_npm = self._parse_version(npm.stdout.strip())
        required_npm = self._parse_version(self.catalog.npm_minimum)
        if actual_npm < required_npm:
            raise ProjectInitializationError(
                "PROJECT_PROFILE_INVALID",
                f"npm {self.catalog.npm_minimum}+ is required, found {npm.stdout.strip()!r}.",
            )

    def _prepare_staging(self) -> None:
        self._cleanup_staging()
        self.staged_project.mkdir(parents=True, exist_ok=False)

    def _run_official_initializers(self) -> None:
        self._run(["npm", "init", "-y"], cwd=self.staged_project)
        self._run(
            [
                "npm",
                "create",
                f"vite@{self.catalog.create_vite}",
                "frontend",
                "--",
                "--template",
                "react-ts",
            ],
            cwd=self.staged_project,
        )
        for workspace in ("backend", "shared"):
            workspace_root = self.staged_project / workspace
            workspace_root.mkdir(parents=True, exist_ok=False)
            self._run(["npm", "init", "-y"], cwd=workspace_root)
        for workspace in ("backend", "shared"):
            self._run(
                [
                    "npm",
                    "exec",
                    "--yes",
                    f"--package=typescript@{self.catalog.typescript}",
                    "--",
                    "tsc",
                    "--init",
                ],
                cwd=self.staged_project / workspace,
            )

    def _normalize_workspace(self) -> None:
        self._write_json(self.staged_project / "package.json", self._root_package())
        self._write_json(self.staged_project / "frontend" / "package.json", self._frontend_package())
        self._write_json(self.staged_project / "backend" / "package.json", self._backend_package())
        self._write_json(self.staged_project / "shared" / "package.json", self._shared_package())

        frontend_root = self.staged_project / "frontend"
        for relative in ("public", "eslint.config.js", "README.md", ".gitignore"):
            self._remove_exact(frontend_root / relative)
        frontend_src = self.staged_project / "frontend" / "src"
        for relative in ("assets", "App.css"):
            self._remove_exact(frontend_src / relative)
        for directory in ("api", "app", "components", "pages"):
            (frontend_src / directory).mkdir(parents=True, exist_ok=True)
        (frontend_src / "app" / "stores").mkdir(parents=True, exist_ok=True)
        self._write_text(
            frontend_src / "App.tsx",
            'export default function App() {\n  return <div id="arc-app" />;\n}\n',
        )
        self._write_text(
            frontend_src / "index.css",
            ':root { font-family: system-ui, sans-serif; color-scheme: light; }\n* { box-sizing: border-box; }\nbody { margin: 0; }\n',
        )
        self._write_text(
            frontend_src / "main.tsx",
            'import { StrictMode } from "react";\n'
            'import { createRoot } from "react-dom/client";\n'
            'import "./index.css";\n'
            'import App from "./App";\n\n'
            'createRoot(document.getElementById("root")!).render(\n'
            '  <StrictMode>\n    <App />\n  </StrictMode>,\n);\n',
        )

        backend_src = self.staged_project / "backend" / "src"
        backend_src.mkdir(parents=True, exist_ok=True)
        self._write_json(self.staged_project / "backend" / "tsconfig.json", self._backend_tsconfig())
        self._write_text(
            self.staged_project / "backend" / "drizzle.config.ts",
            'import { defineConfig } from "drizzle-kit";\n\n'
            'export default defineConfig({\n'
            '  dialect: "sqlite",\n'
            '  schema: "./src/db/schema/index.ts",\n'
            '  out: "./drizzle",\n'
            '  dbCredentials: {\n'
            '    url: process.env.DATABASE_URL ?? "./data/app.db",\n'
            '  },\n'
            '});\n',
        )

        shared_src = self.staged_project / "shared" / "src"
        (shared_src / "contracts").mkdir(parents=True, exist_ok=True)
        self._write_text(shared_src / "index.ts", "export {};\n")
        self._write_json(self.staged_project / "shared" / "tsconfig.json", self._shared_tsconfig())

        self._write_text(
            self.staged_project / ".env.example",
            f"PORT={self.web_port}\nDATABASE_URL=./data/app.db\n",
        )
        self._write_text(
            self.staged_project / ".gitignore",
            "node_modules/\ndist/\ndata/\n.env\n.arc/staging/\n",
        )
        for lockfile in self.staged_project.glob("*/package-lock.json"):
            self._remove_exact(lockfile)

    def _create_lockfile(self) -> None:
        self._run(
            ["npm", "install", "--package-lock-only", "--ignore-scripts"],
            cwd=self.staged_project,
        )

    def _install_promoted_workspace(self) -> None:
        node_modules = self.output_root / "node_modules"
        if node_modules.exists():
            raise ProjectInitializationError(
                "PROJECT_TARGET_CONFLICT",
                f"Project target appeared during initialization: {node_modules}",
            )
        # npm workspace links are absolute junctions on Windows. They must be
        # created after promotion or they continue pointing into .arc/staging.
        self._promoted_targets.append(node_modules)
        self._run(["npm", "ci"], cwd=self.output_root)

        shared_link = node_modules / "@arc" / "shared"
        expected_shared = (self.output_root / "shared").resolve()
        actual_shared = shared_link.resolve()
        if not shared_link.exists() or actual_shared != expected_shared:
            raise ProjectInitializationError(
                "PROJECT_INSTALL_FAILED",
                "npm did not create a valid @arc/shared workspace link in the promoted project: "
                f"expected {expected_shared}, resolved {actual_shared}.",
            )

    def _validate_staged_project(self) -> None:
        expected = (
            "package.json",
            "package-lock.json",
            "frontend/package.json",
            "frontend/src/main.tsx",
            "backend/package.json",
            "backend/tsconfig.json",
            "backend/drizzle.config.ts",
            "shared/package.json",
            "shared/src/index.ts",
        )
        missing = [relative for relative in expected if not (self.staged_project / relative).exists()]
        if missing:
            raise ProjectInitializationError(
                "PROJECT_NORMALIZATION_FAILED",
                f"Normalized project is missing: {', '.join(missing)}",
            )
        lockfile_candidates = (
            self.staged_project / "package-lock.json",
            *self.staged_project.glob("*/package-lock.json"),
        )
        lockfiles = [path for path in lockfile_candidates if path.is_file()]
        expected_lockfile = self.staged_project / "package-lock.json"
        if len(lockfiles) != 1 or lockfiles[0] != expected_lockfile:
            relative = [path.relative_to(self.staged_project).as_posix() for path in lockfiles]
            raise ProjectInitializationError(
                "PROJECT_LOCKFILE_FAILED",
                f"Expected exactly one root lockfile, found: {relative}",
            )
        backend_tsconfig = self._read_json(self.staged_project / "backend" / "tsconfig.json")
        if backend_tsconfig.get("references") != [{"path": "../shared"}]:
            raise ProjectInitializationError(
                "PROJECT_NORMALIZATION_FAILED",
                "backend/tsconfig.json must reference the shared TypeScript project.",
            )
        for package_path in self.staged_project.glob("*/package.json"):
            package = self._read_json(package_path)
            for section in ("dependencies", "devDependencies"):
                for name, version in package.get(section, {}).items():
                    if name == "@arc/shared" and version == "*":
                        continue
                    if not isinstance(version, str) or version.startswith(("^", "~")) or version == "latest":
                        raise ProjectInitializationError(
                            "PROJECT_PROFILE_INVALID",
                            f"Dependency {name!r} in {package_path.name} is not pinned: {version!r}",
                        )

    def _promote(self) -> None:
        for item in sorted(self.staged_project.iterdir(), key=lambda value: value.name):
            target = self.output_root / item.name
            if target.exists():
                raise ProjectInitializationError(
                    "PROJECT_TARGET_CONFLICT",
                    f"Project target appeared during initialization: {target}",
                )
            try:
                shutil.move(str(item), str(target))
            except OSError as exc:
                raise ProjectInitializationError(
                    "PROJECT_SCAFFOLD_FAILED",
                    f"Cannot promote staged project target {item.name}: {exc}",
                ) from exc
            self._promoted_targets.append(target)

    def _emit_manifests(self) -> dict[str, str]:
        stack_profile_path = self.project_artifact_root / "stack-profile.json"
        project_manifest_path = self.project_artifact_root / "project-manifest.json"
        stack_profile = {
            "schemaVersion": 1,
            "id": PROJECT_PROFILE_ID,
            "applicationType": "web",
            "packageManager": "npm",
            "workspaceLayout": "npm-workspaces",
            "frontend": {
                "language": "typescript",
                "buildTool": "vite",
                "ui": "react-18",
                "routing": "react-router",
            },
            "backend": {
                "language": "typescript",
                "runtime": "node",
                "moduleSystem": "NodeNext",
                "webFramework": "express",
                "servesFrontendDist": True,
                "validation": "zod",
                "database": "sqlite",
                "orm": "drizzle",
            },
            "shared": {"contracts": "zod", "typescriptProject": True},
            "versions": self.catalog.to_dict(),
        }
        project_manifest = {
            "schemaVersion": 1,
            "status": PROJECT_STATUS,
            "profile": PROJECT_PROFILE_ID,
            "layout": "npm-workspaces",
            "packageManager": "npm",
            "lockfile": "package-lock.json",
            "deployment": {
                "workingDirectory": "backend",
                "startCommand": "npm run start",
                "serverEntry": "backend/dist/server.js",
                "frontendDist": "frontend/dist",
                "spaFallback": "frontend/dist/index.html",
            },
            "workspaces": {
                "frontend": {"root": "frontend", "sourceRoot": "frontend/src"},
                "backend": {
                    "root": "backend",
                    "sourceRoot": "backend/src",
                    "moduleSystem": "NodeNext",
                },
                "shared": {
                    "root": "shared",
                    "sourceRoot": "shared/src",
                    "packageName": "@arc/shared",
                },
            },
            "owners": {
                "frontend/src/main.tsx": "PROJECT_INITIALIZER",
                "frontend/src/App.tsx": "FRONTEND_SKELETON_COMPILER",
                "frontend/vite.config.ts": "FRONTEND_SKELETON_COMPILER",
                "shared/src/index.ts": "COMPILER",
                "backend/src": "SKELETON_COMPILER",
                "shared/src/contracts": "SKELETON_COMPILER",
            },
            "allowedOutputRoots": {
                "skeleton": ["backend/src", "shared/src/contracts", "shared/src/index.ts"],
                "frontendSkeleton": [
                    "frontend/vite.config.ts",
                    "frontend/src/App.tsx",
                    "frontend/src/app",
                    "frontend/src/api",
                    "frontend/src/components",
                    "frontend/src/pages",
                ],
            },
        }
        self._validate_manifest_paths(project_manifest)
        self._project_artifact_owned = True
        write_json_atomic(stack_profile_path, stack_profile)
        write_json_atomic(project_manifest_path, project_manifest)
        return {
            "project_stack_profile": str(stack_profile_path),
            "project_manifest": str(project_manifest_path),
        }

    def _validate_manifest_paths(self, manifest: dict[str, Any]) -> None:
        values: list[str] = [str(manifest["lockfile"])]
        deployment = manifest.get("deployment", {})
        values.extend(
            str(deployment[key])
            for key in ("workingDirectory", "serverEntry", "frontendDist", "spaFallback")
            if deployment.get(key)
        )
        for workspace in manifest["workspaces"].values():
            values.extend(value for key, value in workspace.items() if key.endswith("Root") or key == "root")
        for roots in manifest["allowedOutputRoots"].values():
            values.extend(roots)
        values.extend(manifest["owners"].keys())
        for relative in values:
            candidate = (self.output_root / relative).resolve()
            if candidate != self.output_root and self.output_root not in candidate.parents:
                raise ProjectInitializationError(
                    "PROJECT_MANIFEST_INVALID",
                    f"Manifest path escapes the output root: {relative}",
                )
        for roots in manifest["allowedOutputRoots"].values():
            for relative in roots:
                if not (self.output_root / relative).exists():
                    raise ProjectInitializationError(
                        "PROJECT_MANIFEST_INVALID",
                        f"Declared output root does not exist: {relative}",
                    )

    def _run(self, args: Sequence[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        executable = shutil.which(args[0], path=self.environment.get("PATH"))
        if executable is None:
            raise ProjectInitializationError(
                "PROJECT_SCAFFOLD_FAILED",
                f"Required command is unavailable: {args[0]}",
            )
        command = [executable, *args[1:]]
        command_environment = dict(self.environment)
        command_environment.setdefault("npm_config_yes", "true")
        command_environment.setdefault("npm_config_audit", "false")
        command_environment.setdefault("npm_config_fund", "false")
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                env=command_environment,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=900,
            )
        except FileNotFoundError as exc:
            raise ProjectInitializationError(
                "PROJECT_SCAFFOLD_FAILED",
                f"Required command is unavailable: {args[0]}",
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ProjectInitializationError(
                "PROJECT_SCAFFOLD_FAILED",
                f"Command {list(args)!r} exceeded the 900 second timeout.",
            ) from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            if len(detail) > 2000:
                detail = detail[-2000:]
            raise ProjectInitializationError(
                "PROJECT_SCAFFOLD_FAILED",
                f"Command {list(args)!r} exited with {completed.returncode}: {detail}",
            )
        return completed

    def _root_package(self) -> dict[str, Any]:
        return {
            "name": "generated-application",
            "private": True,
            "workspaces": ["frontend", "backend", "shared"],
            "scripts": {
                "build": (
                    "npm run build -w @arc/shared && npm run build -w @arc/frontend "
                    "&& npm run build -w @arc/backend"
                ),
                "typecheck": (
                    "npm run build -w @arc/shared && npm run typecheck -w @arc/frontend "
                    "&& npm run typecheck -w @arc/backend"
                ),
            },
            "engines": {"node": f">={self.catalog.node_major}"},
        }

    def _frontend_package(self) -> dict[str, Any]:
        return {
            "name": "@arc/frontend",
            "private": True,
            "version": "0.0.0",
            "type": "module",
            "scripts": {"dev": "vite", "build": "tsc -b && vite build", "typecheck": "tsc -b"},
            "dependencies": {
                "@arc/shared": "*",
                "react": self.catalog.react,
                "react-dom": self.catalog.react_dom,
                "react-router-dom": self.catalog.react_router_dom,
            },
            "devDependencies": {
                "@types/react": self.catalog.types_react,
                "@types/react-dom": self.catalog.types_react_dom,
                "@types/node": self.catalog.types_node,
                "@vitejs/plugin-react": self.catalog.vite_react_plugin,
                "typescript": self.catalog.typescript,
                "vite": self.catalog.vite,
            },
        }

    def _backend_package(self) -> dict[str, Any]:
        return {
            "name": "@arc/backend",
            "private": True,
            "version": "0.0.0",
            "type": "module",
            "scripts": {
                "dev": "tsx watch src/server.ts",
                "build": "tsc -b tsconfig.json",
                "typecheck": (
                    "npm run build -w @arc/shared && tsc --noEmit -p tsconfig.json"
                ),
                "start": "node ./dist/server.js",
                "db:generate": "drizzle-kit generate",
                "db:migrate": "drizzle-kit migrate",
            },
            "dependencies": {
                "@arc/shared": "*",
                "better-sqlite3": self.catalog.better_sqlite3,
                "drizzle-orm": self.catalog.drizzle_orm,
                "express": self.catalog.express,
                "zod": self.catalog.zod,
            },
            "devDependencies": {
                "@types/better-sqlite3": self.catalog.types_better_sqlite3,
                "@types/express": self.catalog.types_express,
                "@types/node": self.catalog.types_node,
                "drizzle-kit": self.catalog.drizzle_kit,
                "tsx": self.catalog.tsx,
                "typescript": self.catalog.typescript,
            },
        }

    def _shared_package(self) -> dict[str, Any]:
        return {
            "name": "@arc/shared",
            "private": True,
            "version": "0.0.0",
            "type": "module",
            "exports": {".": {"types": "./dist/index.d.ts", "default": "./dist/index.js"}},
            "scripts": {
                "build": "tsc -b tsconfig.json",
                "typecheck": "tsc --noEmit -p tsconfig.json",
            },
            "dependencies": {"zod": self.catalog.zod},
            "devDependencies": {"typescript": self.catalog.typescript},
        }

    def _backend_tsconfig(self) -> dict[str, Any]:
        config = self._node_tsconfig()
        config["references"] = [{"path": "../shared"}]
        return config

    def _node_tsconfig(self) -> dict[str, Any]:
        return {
            "compilerOptions": {
                "target": "ES2022",
                "module": "NodeNext",
                "moduleResolution": "NodeNext",
                "rootDir": "src",
                "outDir": "dist",
                "strict": True,
                "noUncheckedIndexedAccess": True,
                "exactOptionalPropertyTypes": True,
                "esModuleInterop": True,
                "resolveJsonModule": True,
                "skipLibCheck": True,
                "forceConsistentCasingInFileNames": True,
                "types": ["node"],
            },
            "include": ["src/**/*.ts"],
            "exclude": ["dist", "node_modules"],
        }

    def _shared_tsconfig(self) -> dict[str, Any]:
        config = self._node_tsconfig()
        config["compilerOptions"].update({"declaration": True, "composite": True})
        return config

    def _cleanup_staging(self) -> None:
        if self.staging_root.exists():
            shutil.rmtree(self.staging_root, ignore_errors=True)

    def _rollback_promoted_targets(self) -> None:
        for target in reversed(self._promoted_targets):
            self._remove_exact(target)
        self._promoted_targets.clear()
        if self._project_artifact_owned:
            self._remove_exact(self.project_artifact_root)
            self._project_artifact_owned = False

    @staticmethod
    def _write_json(path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProjectInitializationError(
                "PROJECT_NORMALIZATION_FAILED",
                f"Cannot read normalized JSON file {path}: {exc}",
            ) from exc
        if not isinstance(value, dict):
            raise ProjectInitializationError(
                "PROJECT_NORMALIZATION_FAILED",
                f"Normalized JSON file must contain an object: {path}",
            )
        return value

    @staticmethod
    def _write_text(path: Path, value: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")

    @staticmethod
    def _remove_exact(path: Path) -> None:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()

    @staticmethod
    def _parse_version(value: str) -> tuple[int, int, int]:
        parts = value.split(".")
        if len(parts) < 2:
            raise ProjectInitializationError(
                "PROJECT_PROFILE_INVALID",
                f"Cannot parse toolchain version: {value!r}",
            )
        numbers: list[int] = []
        for part in parts[:3]:
            digits = "".join(character for character in part if character.isdigit())
            if not digits:
                raise ProjectInitializationError(
                    "PROJECT_PROFILE_INVALID",
                    f"Cannot parse toolchain version: {value!r}",
                )
            numbers.append(int(digits))
        while len(numbers) < 3:
            numbers.append(0)
        return numbers[0], numbers[1], numbers[2]
