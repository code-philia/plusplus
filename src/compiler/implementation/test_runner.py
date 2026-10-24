"""Fixed compiler/test commands; every browser start owns an isolated database path."""
import os
import subprocess
import socket
import time
import uuid
import tempfile
from urllib.request import urlopen
from urllib.error import URLError
from pathlib import Path
from utils.encoding import utf8_environment
from utils.jsonio import write_json_atomic
from utils.process import process_group_kwargs, terminate_process_tree
from .test_reports import read_test_report


class TestRunner:
    def __init__(self, root, *, timeout=60):
        self.root = Path(root).resolve()
        self.timeout = max(1, timeout)
        self.support = self.root / 'tests/tdd'
        self._temporary = tempfile.TemporaryDirectory(prefix='.arc-tdd-', dir=self.root)
        self.runtime = Path(self._temporary.name)

    def close(self):
        self._temporary.cleanup()

    def execute(self, argv, *, cwd=None, environment=None, timeout=None):
        env = utf8_environment({**os.environ, **(environment or {})})
        try:
            with tempfile.TemporaryFile(mode='w+', encoding='utf-8', errors='replace') as output:
                process = subprocess.Popen(argv, cwd=cwd or self.root, env=env, stdout=output,
                                           stderr=subprocess.STDOUT, **process_group_kwargs())
                try:
                    code = process.wait(timeout=self.timeout if timeout is None else timeout)
                except subprocess.TimeoutExpired:
                    terminate_process_tree(process)
                    output.seek(0)
                    return {'status': 'FAILED', 'failure_kind': 'execution_timeout', 'command': argv, 'error': 'Command timed out',
                            'output': output.read()[-18000:]}
                output.seek(0)
                return {'status': 'PASSED' if code == 0 else 'FAILED', 'command': argv, 'exit_code': code,
                        'output': output.read()[-18000:]}
        except OSError as exc:
            return {'status': 'FAILED', 'failure_kind': 'infrastructure', 'command': argv, 'error': str(exc)}

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
  outputDir: process.env.ARC_TDD_RESULTS_DIR ?? "../test-results",
  timeout: 30000, use: { baseURL: "http://127.0.0.1:" + port },
  webServer: process.env.ARC_TDD_EXTERNAL_SERVER === "1" ? undefined : { command: "node backend/dist/server.js", cwd: "../..",
    url: "http://127.0.0.1:" + port + "/__arc/health", reuseExistingServer: false,
    env: { PORT: port, NODE_ENV: "test", DATABASE_URL: process.env.DATABASE_URL ?? ":memory:" },
    timeout: 30000,
  },
});
''',
        }
        for name, content in configs.items():
            (self.support / name).write_text(content, encoding='utf-8')

    def check_environment(self, *, browser=False):
        """Probe runner dependencies once, independently of generated application code."""
        try:
            self.tool('typescript', 'bin/tsc')
            self.tool('vitest', 'vitest.mjs')
            if browser:
                self.tool('vite', 'bin/vite.js')
                playwright = self.tool('@playwright/test', 'index.js')
                script = ('const { chromium } = require(process.argv[1]); '
                          '(async () => { const browser = await chromium.launch(); '
                          'await browser.close(); })().catch(error => { console.error(error); process.exit(1); });')
                result = self.execute(['node', '-e', script, playwright])
            else:
                result = self.execute(['node', '--version'])
        except (OSError, ValueError) as exc:
            result = {'status': 'FAILED', 'error': str(exc)}
        if result['status'] != 'PASSED':
            result['failure_kind'] = 'infrastructure'
        return [result]

    def compile(self):
        from compiler.lowering.planning import relative_import
        compiler = self.tool('typescript', 'bin/tsc')
        artifact = self.runtime
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
                'include': [str(self.root / layer / 'src/**/*')], 'exclude': [], 'references': []})
            result = self.execute(['node', compiler, '-p', str(config)])
            results.append(result)
            if result['status'] != 'PASSED':
                break
        return results

    def check_tests(self, files):
        if not files:
            return [{'status': 'FAILED', 'error': 'No acceptance test targets'}]
        compiler = self.tool('typescript', 'bin/tsc')
        config = self.runtime / 'tests.tsconfig.json'
        write_json_atomic(config, {'compilerOptions': {
            'target': 'ES2022', 'module': 'NodeNext', 'moduleResolution': 'NodeNext', 'noEmit': True,
            'strict': True, 'esModuleInterop': True, 'skipLibCheck': True, 'jsx': 'react-jsx',
        }, 'files': [str(self.root / path) for path in files], 'include': []})
        return [self.execute(['node', compiler, '-p', str(config)])]

    def run(self, files, *, port=33100, red=False):
        with tempfile.TemporaryDirectory(prefix='arc-test-run-') as directory:
            return self.run_tests_in_artifact(files, port=port, red=red, artifact=Path(directory))

    def run_tests_in_artifact(self, files, *, port, red, artifact):
        self.prepare()
        results = []
        # ======= Partition acceptance tests into unit and browser runs ========
        unit = [path for path in files if not path.startswith('tests/e2e/')]
        browser = [path for path in files if path.startswith('tests/e2e/')]
        # ========== Run unit tests and read their structured report ===========
        if unit:
            report = artifact / ('vitest-' + uuid.uuid4().hex + '.json')
            result = self.execute(['node', self.tool('vitest', 'vitest.mjs'), 'run', '--config',
                'tests/tdd/vitest.config.ts', '--reporter=json', '--outputFile=' + str(report), *unit],
                environment={'DATABASE_URL': ':memory:', 'NODE_ENV': 'test'})
            self.test_report(result, report, 'vitest', len(unit))
            result['test_files'] = unit
            results.append(result)
        # ========== Build the application before browser acceptance ===========
        if browser:
            for command in (['node', self.tool('typescript', 'bin/tsc'), '-p', 'backend/tsconfig.json'],
                            ['node', self.tool('vite', 'bin/vite.js'), 'build']):
                result = self.execute(command, cwd=self.root / 'frontend' if 'build' == command[-1] else self.root)
                results.append(result)
                if result['status'] != 'PASSED':
                    return results
            db_path = str(artifact / ('run-' + uuid.uuid4().hex + '.db'))
            report = artifact / ('playwright-' + uuid.uuid4().hex + '.json')
            result = self.browser_run(browser, port, db_path, report, red)
            result['test_files'] = browser
            results.append(result)
        return results

    def browser_run(self, browser, port, db_path, report, red):
        # ====== Own application startup, browser execution, and cleanup =======
        # Own the server directly: Playwright webServer may start a detached process group.
        # Never kill a preexisting listener belonging to another compilation/application.
        try:
            selected_port = self.select_browser_port(port)
        except OSError as exc:
            return {'status': 'FAILED', 'failure_kind': 'infrastructure', 'error': str(exc)}
        environment = {'DATABASE_URL': db_path, 'PLAYWRIGHT_JSON_OUTPUT_FILE': str(report),
                       'ARC_TDD_RESULTS_DIR': str(report.parent / 'results'),
                       'ARC_TDD_PORT': str(selected_port), 'PORT': str(selected_port),
                       'ARC_TDD_EXTERNAL_SERVER': '1', 'NODE_ENV': 'test', 'ARC_VALIDATION_PREPARED': '0'}
        server_log = report.with_suffix('.server.log')
        server = None
        ready = False
        try:
            with server_log.open('w', encoding='utf-8') as output:
                server = subprocess.Popen(['node', 'backend/dist/server.js'], cwd=self.root,
                    env=utf8_environment({**os.environ, **environment}), stdout=output, stderr=subprocess.STDOUT,
                    **process_group_kwargs())
                ready = self.wait_for_application(server, selected_port)
                argv = ['node', self.tool('@playwright/test', 'cli.js'), 'test', '--config',
                        'tests/tdd/playwright.config.ts', '--reporter=json']
                # RED needs an observed failure, not six sequential locator timeouts.
                if red:
                    argv.append('--max-failures=1')
                limit = max(90, int(os.environ.get('ARC_TDD_BROWSER_TIMEOUT_SECONDS', '600')))
                argv.append('--global-timeout=' + str(limit * 1000))
                result = self.execute(argv + browser, environment=environment, timeout=limit + 30)
                self.test_report(result, report, 'playwright', len(browser))
                result['server_port'] = selected_port
                if result['status'] != 'PASSED':
                    result['application_output'] = server_log.read_text(encoding='utf-8', errors='replace')[-18000:]
                    if server.poll() is not None:
                        result['failure_kind'] = 'application_runtime'
                        result['application_exit_code'] = server.returncode
                return result
        except (OSError, ValueError) as exc:
            kind = 'application_startup' if server is not None and not ready else 'infrastructure'
            return {'status': 'FAILED', 'failure_kind': kind, 'error': str(exc),
                    'exit_code': server.poll() if server is not None else None,
                    'server_port': selected_port,
                    'output': server_log.read_text(encoding='utf-8', errors='replace')[-18000:] if server_log.exists() else ''}
        finally:
            if server is not None:
                terminate_process_tree(server)
                if server.poll() is None:
                    server.wait(timeout=5)

    @staticmethod
    def select_browser_port(port):
        # Release the probe socket before Node starts; never stop an existing listener.
        with socket.socket() as reservation:
            try:
                reservation.bind(('127.0.0.1', port))
            except OSError:
                reservation.bind(('127.0.0.1', 0))
            selected_port = reservation.getsockname()[1]
        return selected_port

    @staticmethod
    def wait_for_application(server, selected_port):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if server.poll() is not None:
                raise ValueError('Test application exited before becoming ready')
            try:
                with urlopen(f'http://127.0.0.1:{selected_port}/__arc/health', timeout=1) as response:
                    if response.status == 200:
                        return True
            except (URLError, OSError):
                pass
            time.sleep(0.1)
        else:
            raise ValueError('Test application startup timed out')

    def test_report(self, result, path, runner, expected_files):
        read_test_report(self.root, result, path, runner, expected_files)


def passed(results):
    return bool(results) and all(row['status'] == 'PASSED' for row in results)
