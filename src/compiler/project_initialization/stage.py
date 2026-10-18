"""Initialize the current React/Express/SQLite stack without replacing existing files."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from utils.jsonio import write_json_atomic
from utils.logging import SynchronousLog
from utils.process import resolve_executable, run_command


class ProjectInitializer:
    def __init__(self, output_root, *, web_port=3000):
        self.root = Path(output_root).resolve()
        self.port = web_port
        self.log = SynchronousLog('ProjectInitializer', workspace_root=self.root)

    def initialize(self, *, install_dependencies=True):
        if not 1 <= self.port <= 65535:
            raise ValueError('Port must be between 1 and 65535')
        root_package = {
            'name': 'arc-application', 'private': True, 'type': 'module',
            'workspaces': ['backend', 'frontend'],
            'scripts': {
                'build': 'npm run build -w @arc/frontend && npm run build -w @arc/backend',
                'start': 'npm run start -w @arc/backend',
                'dev:backend': 'npm run dev -w @arc/backend',
                'dev:frontend': 'npm run dev -w @arc/frontend',
            },
            'devDependencies': {'@playwright/test': '1.55.0', 'vitest': '3.2.4',
                                'supertest': '7.1.4', '@types/supertest': '6.0.3', 'tsx': '4.20.5'},
        }
        backend = {
            'name': '@arc/backend', 'private': True, 'type': 'module',
            'scripts': {'build': 'tsc -p tsconfig.json', 'typecheck': 'tsc --noEmit -p tsconfig.json',
                        'start': 'node ./dist/server.js', 'dev': 'tsx watch src/server.ts'},
            'dependencies': {'express': '5.1.0', 'better-sqlite3': '12.2.0',
                             'drizzle-orm': '0.44.5', 'zod': '4.1.5'},
            'devDependencies': {'typescript': '5.9.2', '@types/node': '22.18.0',
                                '@types/express': '5.0.3', '@types/better-sqlite3': '7.6.13'},
        }
        frontend = {
            'name': '@arc/frontend', 'private': True, 'type': 'module',
            'scripts': {'build': 'tsc --noEmit -p tsconfig.app.json && vite build',
                        'typecheck': 'tsc --noEmit -p tsconfig.app.json', 'dev': 'vite'},
            'dependencies': {'react': '18.3.1', 'react-dom': '18.3.1', 'react-router-dom': '7.8.2'},
            'devDependencies': {'typescript': '5.9.2', '@types/react': '18.3.23',
                                '@types/react-dom': '18.3.7', '@types/node': '22.18.0',
                                'vite': '7.1.4', '@vitejs/plugin-react': '5.0.2',
                                'tailwindcss': '4.1.13', '@tailwindcss/vite': '4.1.13'},
        }
        common = {'target': 'ES2022', 'strict': True, 'esModuleInterop': True, 'skipLibCheck': True}
        sources = {
            'package.json': root_package, 'backend/package.json': backend, 'frontend/package.json': frontend,
            'backend/tsconfig.json': {'compilerOptions': {**common, 'module': 'NodeNext',
                'moduleResolution': 'NodeNext', 'rootDir': 'src', 'outDir': 'dist', 'types': ['node']},
                'include': ['src/**/*.ts']},
            'frontend/tsconfig.app.json': {'compilerOptions': {**common, 'module': 'ESNext',
                'moduleResolution': 'Bundler', 'jsx': 'react-jsx', 'noEmit': True,
                'lib': ['ES2022', 'DOM', 'DOM.Iterable']}, 'include': ['src']},
            'frontend/tsconfig.json': {'extends': './tsconfig.app.json'},
        }
        text_sources = {
            'backend/src/config/server.ts': 'export function serverPort(): number {\n'
                + f'  const value = process.env.PORT ?? "{self.port}";\n'
                + '  const port = Number(value);\n'
                + '  if (!Number.isInteger(port) || port < 0 || port > 65535) {\n'
                + '    throw new Error("PORT must be an integer between 0 and 65535");\n'
                + '  }\n  return port;\n}\n',
            'frontend/index.html': '<!doctype html>\n<html><head><meta charset="UTF-8" />'
                '<meta name="viewport" content="width=device-width, initial-scale=1.0" />'
                '<title>Application</title></head><body><div id="root"></div>'
                '<script type="module" src="/src/main.tsx"></script></body></html>\n',
            'frontend/src/index.css': '@import "tailwindcss";\n',
            'frontend/vite.config.ts': 'import { defineConfig } from "vite";\n'
                'import react from "@vitejs/plugin-react";\nimport tailwindcss from "@tailwindcss/vite";\n'
                'export default defineConfig({ plugins: [react(), tailwindcss()], server: { proxy: '
                + json.dumps({'/api': f'http://127.0.0.1:{self.port}'}) + ' } });\n',
        }
        created = []
        for relative, content in {**sources, **text_sources}.items():
            target = self.root / relative
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, dict):
                write_json_atomic(target, content)
            else:
                target.write_text(content, encoding='utf-8')
            created.append(relative)
        # Hoisted workspace packages are shared by all compilation/test runners.
        commands = ([['npm', 'install', '--no-audit', '--no-fund'],
                     ['npm', 'exec', '--', 'playwright', 'install', 'chromium']]
                    if install_dependencies else [])
        report = {'status': 'INITIALIZING', 'created_files': created, 'port': self.port}
        report_path = self.root / '.arc/project/report.json'
        write_json_atomic(report_path, report)
        try:
            npm = resolve_executable('npm', os.environ) if commands else None
            if commands and npm is None:
                raise ValueError('Node.js/npm is unavailable on PATH; install Node.js and reopen the terminal.')
            for argv in commands:
                self.log.info('Project initialization: ' + ' '.join(argv))
                result = run_command([npm, *argv[1:]], cwd=self.root, environment=os.environ, timeout=600)
                if result.returncode:
                    raise ValueError(f'Project initialization failed: {argv}; '
                                     + (result.stdout + result.stderr)[-4000:])
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            report.update(status='FAILED', errors=[str(exc)])
            write_json_atomic(report_path, report)
            raise ValueError(str(exc)) from exc
        report['status'] = 'PROJECT_INITIALIZED'
        write_json_atomic(report_path, report)
        return report
