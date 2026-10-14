"""Fixed compiler/test commands; every browser start owns an isolated database path."""
import os
import json
import subprocess
from pathlib import Path
from utils.jsonio import write_json_atomic
from utils.process import process_group_kwargs, terminate_process_tree


class TestRunner:
    def __init__(self, root, *, timeout=60):
        self.root = Path(root).resolve()
        self.timeout = max(1, timeout)
        self.support = self.root / 'tests/tdd'

    def execute(self, argv, *, cwd=None, environment=None):
        env = {**os.environ, **(environment or {})}
        log = self.root / '.arc/implementation/command-output.txt'
        log.parent.mkdir(parents=True, exist_ok=True)
        try:
            with log.open('w', encoding='utf-8') as output:
                process = subprocess.Popen(argv, cwd=cwd or self.root, env=env, stdout=output,
                                           stderr=subprocess.STDOUT, **process_group_kwargs())
                try:
                    code = process.wait(timeout=self.timeout)
                except subprocess.TimeoutExpired:
                    terminate_process_tree(process)
                    return {'status': 'FAILED', 'command': argv, 'error': 'Command timed out',
                            'output': log.read_text(encoding='utf-8')[-18000:]}
            return {'status': 'PASSED' if code == 0 else 'FAILED', 'command': argv, 'exit_code': code,
                    'output': log.read_text(encoding='utf-8')[-18000:]}
        except OSError as exc:
            return {'status': 'FAILED', 'command': argv, 'error': str(exc)}

    def tool(self, package, script):
        for base in (self.root / 'node_modules', self.root / 'tests/node_modules', self.root / 'backend/node_modules', self.root / 'frontend/node_modules'):
            path = base / package / script
            if path.is_file():
                return str(path)
        raise ValueError(f'Install project dependency {package} before TDD')

    def prepare(self):
        self.support.mkdir(parents=True, exist_ok=True)
        configs = {
            'vitest.config.ts': '''import { defineConfig } from "vitest/config";
export default defineConfig({ test: {
  include: ["tests/unit/**/*.test.ts", "tests/integration/**/*.test.ts"],
  setupFiles: ["tests/tdd/setup.ts"], fileParallelism: false, maxWorkers: 1,
  testTimeout: 15000, hookTimeout: 15000,
} });
''',
            'setup.ts': '''import { afterAll } from "vitest";
import { createDatabaseClient, bindDatabaseClient } from "../../backend/src/db/client.js";
import { seedDatabase } from "../../backend/src/db/seed.js";
const client = createDatabaseClient(process.env.DATABASE_URL ?? ":memory:");
seedDatabase(client.connection);
bindDatabaseClient(client);
afterAll(() => client.close());
''',
            'playwright.config.ts': '''import { defineConfig } from "@playwright/test";
const port = process.env.ARC_TDD_PORT ?? "33100";
export default defineConfig({
  testDir: "../e2e", workers: 1, fullyParallel: false, retries: 0,
  timeout: 30000, use: { baseURL: "http://127.0.0.1:" + port },
  webServer: { command: "node backend/dist/server.js", cwd: "../..",
    url: "http://127.0.0.1:" + port + "/__arc/health", reuseExistingServer: false,
    env: { PORT: port, NODE_ENV: "test", DATABASE_URL: process.env.DATABASE_URL ?? ":memory:" },
    timeout: 30000,
  },
});
''',
        }
        for name, content in configs.items():
            (self.support / name).write_text(content, encoding='utf-8')

    def compile(self):
        from compiler.lowering.planning import relative_import
        compiler = self.tool('typescript', 'bin/tsc')
        artifact = self.root / '.arc/implementation'
        artifact.mkdir(parents=True, exist_ok=True)
        results = []
        if (self.root / 'shared/tsconfig.json').is_file():
            result = self.execute(['node', compiler, '-b', 'shared/tsconfig.json'])
            results.append(result)
            if result['status'] != 'PASSED':
                return results
        for layer, name in (('backend', 'tsconfig.json'), ('frontend', 'tsconfig.app.json')):
            project = self.root / layer / name
            if not project.is_file():
                return results + [{'status': 'FAILED', 'error': f'Missing {layer}/{name}'}]
            config = artifact / (layer + '.tsconfig.json')
            write_json_atomic(config, {'extends': relative_import(str(config), str(project), '.json'),
                'compilerOptions': {'noEmit': True, 'composite': False, 'incremental': True,
                                    'tsBuildInfoFile': './' + layer + '.tsbuildinfo'},
                'include': [f'../../{layer}/src/**/*'], 'exclude': [], 'references': []})
            result = self.execute(['node', compiler, '-p', str(config)])
            results.append(result)
            if result['status'] != 'PASSED':
                break
        return results

    def check_tests(self, files):
        if not files:
            return [{'status': 'FAILED', 'error': 'No acceptance test targets'}]
        compiler = self.tool('typescript', 'bin/tsc')
        config = self.root / '.arc/implementation/tests.tsconfig.json'
        write_json_atomic(config, {'compilerOptions': {
            'target': 'ES2022', 'module': 'NodeNext', 'moduleResolution': 'NodeNext', 'noEmit': True,
            'strict': True, 'esModuleInterop': True, 'skipLibCheck': True, 'jsx': 'react-jsx',
        }, 'files': [str(self.root / path) for path in files], 'include': []})
        return [self.execute(['node', compiler, '-p', str(config)])]

    def run(self, files, *, port=33100):
        self.prepare()
        results = []
        unit = [path for path in files if not path.startswith('tests/e2e/')]
        browser = [path for path in files if path.startswith('tests/e2e/')]
        artifact = self.root / '.arc/implementation/databases'
        artifact.mkdir(parents=True, exist_ok=True)
        if unit:
            import uuid
            report = artifact / ('vitest-' + uuid.uuid4().hex + '.json')
            result = self.execute(['node', self.tool('vitest', 'vitest.mjs'), 'run', '--config',
                'tests/tdd/vitest.config.ts', '--reporter=json', '--outputFile=' + str(report), *unit],
                environment={'DATABASE_URL': ':memory:', 'NODE_ENV': 'test'})
            self._test_report(result, report, 'vitest', len(unit))
            results.append(result)
        if browser:
            for command in (['node', self.tool('typescript', 'bin/tsc'), '-p', 'backend/tsconfig.json'],
                            ['node', self.tool('vite', 'bin/vite.js'), 'build']):
                result = self.execute(command, cwd=self.root / 'frontend' if 'build' == command[-1] else self.root)
                results.append(result)
                if result['status'] != 'PASSED':
                    return results
            import uuid
            db_path = str(artifact / ('run-' + uuid.uuid4().hex + '.db'))
            report = artifact / ('playwright-' + uuid.uuid4().hex + '.json')
            result = self.execute(['node', self.tool('@playwright/test', 'cli.js'), 'test', '--config',
                'tests/tdd/playwright.config.ts', '--reporter=json', *browser], environment={'DATABASE_URL': db_path,
                    'PLAYWRIGHT_JSON_OUTPUT_FILE': str(report), 'ARC_TDD_PORT': str(port), 'NODE_ENV': 'test'})
            self._test_report(result, report, 'playwright', len(browser))
            results.append(result)
        return results

    @staticmethod
    def _test_report(result, path, runner, expected_files):
        try:
            report = json.loads(path.read_text(encoding='utf-8'))
            failures, collected, executed_files = [], 0, set()
            if runner == 'vitest':
                for suite in report.get('testResults', []):
                    assertions = suite.get('assertionResults', [])
                    collected += len(assertions)
                    if assertions:
                        executed_files.add(suite['name'])
                    failures.extend(message for test in assertions if test['status'] == 'failed'
                                    for message in test.get('failureMessages', []))
            else:
                def visit(suites):
                    nonlocal collected
                    for suite in suites:
                        for spec in suite.get('specs', []):
                            collected += len(spec.get('tests', []))
                            executed_files.add(spec['file'])
                            for test in spec.get('tests', []):
                                for run in test.get('results', []):
                                    if run['status'] in {'failed', 'timedOut'}:
                                        failures.extend(error.get('message', '') for error in run.get('errors', []))
                        visit(suite.get('suites', []))
                visit(report.get('suites', []))
            infrastructure = any(any(marker in message.lower() for marker in
                ('executable doesn\'t exist', 'browser was not found', 'failed to launch', 'cannot find module',
                 'failed to load', 'econnrefused', 'webserver')) for message in failures)
            result['test_evidence'] = {'collected': collected, 'executed_files': len(executed_files),
                                      'failures': failures[-10:], 'behavior_failure': bool(failures) and not infrastructure,
                                      'report_path': str(path)}
            if collected == 0 or len(executed_files) < expected_files:
                result['status'] = 'FAILED'
                result['error'] = 'Acceptance tests were not discovered for every selected file'
                result['test_evidence']['behavior_failure'] = False
        except (OSError, ValueError, KeyError, TypeError) as exc:
            result['status'] = 'FAILED'
            result['error'] = 'Test report unavailable or malformed: ' + str(exc)


def passed(results):
    return bool(results) and all(row['status'] == 'PASSED' for row in results)
