"""Main-experiment generation and independent, single-application evaluation."""
from __future__ import annotations

import argparse
from contextlib import suppress
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
TASKS = ('keep', 'bookstack', 'prestashop', 'stackoverflow', '12306', 'ctrip')
ALIASES = {'gpt': 'gpt', 'deepseek': 'ds'}
PORT_BASES = {'gpt': 33100, 'deepseek': 33200}
PORT_STRIDE = 10  # Reserve the next port for a task's optional alternate-clock world.
DEFAULT_BENCHMARK = ROOT / 'benchmark'
sys.path.insert(0, str(ROOT / 'src'))


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.is_file() else None


def revision(path):
    result = subprocess.run(['git', '-C', str(path), 'rev-parse', 'HEAD'],
                            capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def digest_tree(path):
    digest = hashlib.sha256()
    for item in sorted(p for p in path.rglob('*') if p.is_file()):
        digest.update(item.relative_to(path).as_posix().encode() + b'\0')
        digest.update(item.read_bytes())
        digest.update(b'\0')
    return digest.hexdigest()


def label(value):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', value):
        raise argparse.ArgumentTypeError('Use letters, digits, underscores, or hyphens.')
    return value


def port_number(value):
    number = int(value)
    if not 1 <= number <= 65535:
        raise argparse.ArgumentTypeError('Port must be between 1 and 65535.')
    return number


def require_tools(*names):
    for name in names:
        if not shutil.which(name):
            raise ValueError(f'Missing executable: {name}')


def group_exists(process):
    try:
        os.killpg(process.pid, 0)
        return True
    except ProcessLookupError:
        return False


def stop(process):
    # Children own a process group. Clean descendants even if the leader exited.
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        process.poll()  # Reap an exited leader before checking for descendants.
        if not group_exists(process):
            break
        time.sleep(.1)
    if group_exists(process):
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    process.wait()


def application_port(model, task, base=None):
    port = (PORT_BASES[model] if base is None else base) + PORT_STRIDE * TASKS.index(task)
    # Leave port+1 available for an optional alternate-clock deployment.
    if not 1 <= port < 65535:
        raise ValueError('Application port and reserved port must fit within 1..65535.')
    return port


def evaluation_port(args, app):
    record = read_json(app / '.rex/generation.json') or {}
    if record.get('task') and record['task'] != args.task:
        raise ValueError(f'Application task is {record["task"]}, not {args.task}.')
    if args.model and record.get('profile') and args.model != record['profile']:
        raise ValueError('Selected --model does not match the generated application profile.')
    if args.port is not None:
        return args.port
    if record.get('port') is not None:
        return port_number(record['port'])
    model = args.model
    if model is None:
        for profile, alias in ALIASES.items():
            if app.name.startswith(f'{args.task}-{alias}-'):
                model = profile
                break
    if model is None:
        raise ValueError('Cannot infer the application port; provide --model or --port.')
    return application_port(model, args.task)


def run(command, cwd, log, environment=None):
    print(f'Log: {log}', flush=True)
    with log.open('a', encoding='utf-8') as stream:
        stream.write('\n$ ' + ' '.join(map(str, command)) + '\n')
        stream.flush()
        process = subprocess.Popen(list(map(str, command)), cwd=cwd,
                                   env=environment, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True,
                                   encoding='utf-8', errors='replace', start_new_session=True)
        try:
            for line in process.stdout:
                print(line, end='', flush=True)
                stream.write(line)
                stream.flush()
            return process.wait()
        finally:
            process.stdout.close()
            stop(process)


def checked(command, cwd, log, environment=None):
    code = run(command, cwd, log, environment)
    if code:
        raise ValueError(f'Command exited {code}; see {log}')


def benchmark_root(value):
    path = Path(value).expanduser().resolve()
    return path


def requirement_file(directory):
    for name in ('requirements.yaml', 'requirements.yml'):
        if (directory / name).is_file():
            return directory / name
    raise ValueError(f'Missing requirements.yaml: {directory}')


def generation_paths(args, task):
    stem = f'{task}-{ALIASES[args.model]}-{args.run_id}'
    output = Path(args.output).expanduser().resolve() if getattr(args, 'output', None) else Path(args.output_root).expanduser().resolve() / stem
    records = Path(args.records_root).expanduser().resolve() / 'generation' / stem
    return output, records


def generation_preflight(args, tasks):
    require_tools('git', 'node', 'npm')
    args.benchmark_root = benchmark_root(args.benchmark_root)
    # --model selects its bundled profile, independently of a stale ARC_ENV_FILE.
    env_path = args.env_file or ROOT / f'.env.{args.model}'
    args.env_file = Path(env_path).expanduser().resolve()
    if not args.env_file.is_file():
        raise ValueError(f'Missing model profile: {args.env_file}; use the bundled .env.gpt or .env.deepseek.')
    os.environ['ARC_ENV_FILE'] = str(args.env_file)
    from dotenv import dotenv_values
    settings = {**dotenv_values(args.env_file), **os.environ}
    required = ('OPENAI_BASE_URL', 'OPENAI_API_KEY', 'MODEL',
                'VISUAL_BASE_URL', 'VISUAL_API_KEY', 'VISUAL_MODEL')
    missing = [key for key in required if not str(settings.get(key) or '').strip()]
    if missing:
        raise ValueError(f'Complete {args.env_file}: ' + ', '.join(missing)
                         + '. Configure both main and vision-capable model endpoints before generation.')
    for task in tasks:
        requirement_file(args.benchmark_root / 'input' / task)
        output, records = generation_paths(args, task)
        if output.exists() or records.exists():
            raise ValueError(f'Run already exists: {output} or {records}. Choose a fresh --run-id/output.')


def generate_one(args, task, port):
    from dotenv import dotenv_values
    output, records = generation_paths(args, task)
    inputs = args.benchmark_root / 'input' / task
    records.mkdir(parents=True, exist_ok=False)
    environment = {**os.environ, 'ARC_ENV_FILE': str(args.env_file), 'PYTHONUNBUFFERED': '1'}
    settings = {**dotenv_values(args.env_file), **os.environ}
    settings['VISUAL_MODEL'] = settings.get('VISUAL_MODEL') or settings.get('MODEL')
    command = [sys.executable, ROOT / 'src/main.py', 'compile', inputs,
               '-o', output, '--port', str(port)]
    # External benchmark preparation/deployment happens only in test_app.sh.
    metadata = {'status': 'RUNNING', 'task': task, 'profile': args.model,
                'run_id': args.run_id, 'output': str(output), 'port': port,
                'started_at': now(), 'compiler_revision': revision(ROOT),
                'benchmark_revision': revision(args.benchmark_root),
                'input_sha256': digest_tree(inputs), 'env_file': str(args.env_file),
                'settings': {key: settings.get(key, default) for key, default in {
                    'MODEL': None, 'VISUAL_MODEL': settings.get('MODEL'), 'MODEL_ATTEMPT': '5',
                    'ARC_TDD_MAX_ITERATIONS': '5', 'ARC_TDD_LOAD_ROUNDS': '12'}.items()},
                'command': list(map(str, command)), 'independent_tests_executed': False}
    write_json(records / 'run.json', metadata)
    started = time.monotonic()
    try:
        code = run(command, ROOT, records / 'console.log', environment)
        metadata.update(status='COMMAND_COMPLETED' if code == 0 else 'FAILED', exit_code=code)
        return code
    except BaseException as exc:
        metadata.update(status='INTERRUPTED' if isinstance(exc, KeyboardInterrupt) else 'FAILED',
                        error=str(exc))
        raise
    finally:
        metadata.update(finished_at=now(), duration_seconds=round(time.monotonic() - started, 3),
                        workflow_report=read_json(output / '.arc/workflow/report.json'))
        write_json(records / 'run.json', metadata)
        if output.is_dir():
            write_json(output / '.rex/generation.json', {
                'task': task, 'profile': args.model, 'run_id': args.run_id, 'port': port,
                'generation_record': str(records / 'run.json')})
        print(f'Generation record: {records / "run.json"}', flush=True)


def generate(args):
    generation_preflight(args, [args.task])
    port = args.port if args.port is not None else application_port(args.model, args.task)
    ensure_free_port(port)
    return generate_one(args, args.task, port)


def batch(args):
    tasks = args.tasks.split(',') if args.tasks else list(TASKS)
    if len(set(tasks)) != len(tasks) or any(task not in TASKS for task in tasks):
        raise ValueError('--tasks must be distinct, comma-separated benchmark task names.')
    generation_preflight(args, tasks)
    ports = {task: application_port(args.model, task, args.base_port) for task in tasks}
    for port in ports.values():
        ensure_free_port(port)
    record_path = Path(args.records_root).expanduser().resolve() / 'batches' / f'{ALIASES[args.model]}-{args.run_id}.json'
    if record_path.exists():
        raise ValueError(f'Batch record exists: {record_path}; choose a fresh --run-id.')
    record = {'profile': args.model, 'run_id': args.run_id, 'started_at': now(),
              'independent_tests_executed': False, 'status': 'RUNNING', 'ports': ports, 'runs': []}
    write_json(record_path, record)
    try:
        for task in tasks:
            try:
                ensure_free_port(ports[task])
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
                record['runs'].append({'task': task, 'port': ports[task], 'exit_code': 1, 'error': str(exc)})
                write_json(record_path, record)
                continue
            code = generate_one(args, task, ports[task])
            output, records = generation_paths(args, task)
            record['runs'].append({'task': task, 'port': ports[task], 'exit_code': code, 'output': str(output),
                                   'record': str(records / 'run.json')})
            write_json(record_path, record)
        record['status'] = 'COMMANDS_COMPLETED' if all(row['exit_code'] == 0 for row in record['runs']) else 'FAILED'
        return 0 if record['status'] == 'COMMANDS_COMPLETED' else 1
    except BaseException as exc:
        record.update(status='INTERRUPTED' if isinstance(exc, KeyboardInterrupt) else 'FAILED', error=str(exc))
        raise
    finally:
        record['finished_at'] = now()
        write_json(record_path, record)
        print(f'Batch record: {record_path}')


PLAYWRIGHT_CONFIG = '''import { defineConfig, devices } from '@playwright/test';
const baseURL = process.env.PLAYWRIGHT_BASE_URL || __REX_BASE_URL__;
const runId = process.env.PLAYWRIGHT_RUN_ID ||= __REX_RUN_ID__;
for (const key of ['KEEP_RUN_ID', 'BOOKSTACK_RUN_ID', 'CTRIP_RUN_ID', 'SO_RUN_ID', 'RAIL_RUN_ID']) {
  process.env[key] ||= runId;
}
process.env.SO_BASE_URL ||= baseURL;
const expired = /Refuse an expired comment edit/i;
export default defineConfig({
  testDir: './tests', testMatch: '**/*.spec.ts', fullyParallel: false,
  workers: 1, retries: 0, forbidOnly: true, timeout: 10_000,
  expect: { timeout: 10_000 }, outputDir: './test-results',
  reporter: [['list'], ['json', { outputFile: './report.json' }],
    ['html', { outputFolder: './playwright-report', open: 'never' }]],
  use: { baseURL, timezoneId: process.env.REX_TEST_TIMEZONE || __REX_TIMEZONE__,
    actionTimeout: 15_000, navigationTimeout: 30_000,
    trace: 'retain-on-failure', screenshot: 'only-on-failure', video: 'retain-on-failure' },
  projects: process.env.SO_EXPIRED_BASE_URL ? [
    { name: 'chromium', grepInvert: expired, use: { ...devices['Desktop Chrome'] } },
    { name: 'expired-comment', grep: expired,
      use: { ...devices['Desktop Chrome'], baseURL: process.env.SO_EXPIRED_BASE_URL } }
  ] : [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }]
});
'''


def ensure_free_port(port):
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(('0.0.0.0', port))
        except OSError as exc:
            raise ValueError(f'Port {port} is already in use; choose another --port.') from exc


def wait_ready(process, url, timeout):
    deadline = time.monotonic() + timeout
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while process.poll() is None and time.monotonic() < deadline:
        try:
            with opener.open(url + '/__arc/health', timeout=1) as response:
                if response.status == 200 and process.poll() is None:
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(.2)
    raise ValueError(f'Application did not become ready at {url}; inspect application logs.')


def evaluate(args):
    import yaml
    from compiler.validation_preparation import ValidationPreparationPass
    from compiler.validation_preparation.runtime import normalize_environment

    require_tools('git', 'node', 'npm')
    app = Path(args.app).expanduser().resolve()
    bench = benchmark_root(args.benchmark_root)
    tests = Path(args.tests_dir).expanduser().resolve() if args.tests_dir else bench / 'test' / args.task
    if not (app / 'backend/src/server.ts').is_file() or not (app / 'package.json').is_file():
        raise ValueError(f'Expected a generated Rex application with backend/src/server.ts: {app}')
    args.port = evaluation_port(args, app)
    print(f'Evaluation target: {args.task} at http://127.0.0.1:{args.port}', flush=True)
    if not tests.is_dir() or not list(tests.rglob('*.spec.ts')):
        raise ValueError(f'Independent test suite is missing: {tests}')
    # Use the application's normalized generation input when available.
    ir = read_json(app / '.arc/preprocessing/requirement_ir.json')
    if ir is None:
        ir = yaml.safe_load(requirement_file(bench / 'input' / args.task).read_text(encoding='utf-8-sig'))
    run_id = args.eval_id or datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    destination = app / '.rex/evaluation' / run_id
    if destination.exists():
        raise ValueError(f'Evaluation exists: {destination}; choose a fresh --eval-id.')
    ensure_free_port(args.port)
    expired_case = args.task == 'stackoverflow' and any(
        re.search(r'Refuse an expired comment edit', p.read_text(encoding='utf-8-sig'), re.I)
        for p in tests.rglob('*.spec.ts'))
    if expired_case:
        if args.port == 65535 or not ir.get('environment', {}).get('expired_comment_now'):
            raise ValueError('Expired-comment evaluation needs port+1 and environment.expired_comment_now.')
        ensure_free_port(args.port + 1)
    destination.mkdir(parents=True, exist_ok=False)
    console = destination / 'console.log'
    url = f'http://127.0.0.1:{args.port}'
    environment = {**os.environ, 'PLAYWRIGHT_BASE_URL': url, 'SO_BASE_URL': url,
                   'REX_TEST_TIMEZONE': ir.get('environment', {}).get('timezone', 'UTC'),
                   'PLAYWRIGHT_RUN_ID': run_id, 'CI': '1'}
    for key in ('KEEP_RUN_ID', 'BOOKSTACK_RUN_ID', 'CTRIP_RUN_ID', 'SO_RUN_ID', 'RAIL_RUN_ID'):
        environment[key] = run_id
    # These targets belong to this invocation, not a previously exported test deployment.
    environment.pop('RAIL_BASE_URL_TEMPLATE', None)
    environment.pop('SO_EXPIRED_BASE_URL', None)
    metadata = {'status': 'RUNNING', 'started_at': now(), 'task': args.task, 'app': str(app),
                'url': url, 'eval_id': run_id, 'tests_source': str(tests),
                'tests_sha256': digest_tree(tests),
                'requirement_ir_sha256': hashlib.sha256(
                    json.dumps(ir, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                'compiler_revision': revision(ROOT),
                'benchmark_revision': revision(bench), 'clock_environment': ir.get('environment', {}),
                'workers': 1, 'retries': 0, 'generation_executed': False}
    write_json(destination / 'summary.json', metadata)
    processes, streams, service_ports = [], [], []
    started = time.monotonic()
    try:
        if not (app / 'node_modules').is_dir():
            checked(['npm', 'ci' if (app / 'package-lock.json').is_file() else 'install',
                     '--no-audit', '--no-fund'], app, console)
        # Adds deterministic clock/timezone only; ordinary startup owns reset/seed.
        # No model calls or independent-test-driven application repair.
        ValidationPreparationPass(None, app).generate(ir)
        # The preparation pass builds before patching the backend start script.
        # Direct Node entrypoints below avoid extra builds during deployment.
        worlds = [(args.port, 'application', None)]
        if expired_case:
            worlds.append((args.port + 1, 'expired-comment', ir['environment']['expired_comment_now']))
            environment['SO_EXPIRED_BASE_URL'] = f'http://127.0.0.1:{args.port + 1}'
        for port, name, clock in worlds:
            ensure_free_port(port)
            runtime = normalize_environment({**ir.get('environment', {}),
                **({'benchmark_now': clock, 'clock_mode': 'frozen'} if clock else {})})
            child_env = {**os.environ, 'PORT': str(port), 'NODE_ENV': 'test',
                         'DATABASE_URL': str(destination / f'{name}.db'),
                         'ARC_VALIDATION_PREPARED': '0', 'REX_VALIDATION_ENV': json.dumps(runtime)}
            if runtime.get('timezone'):
                child_env['TZ'] = runtime['timezone']
            bootstrap = ('import { installValidationRuntime } from "./dist/validation/runtime.js";'
                         'installValidationRuntime(JSON.parse(process.env.REX_VALIDATION_ENV));'
                         'await import("./dist/server.js");')
            stream = (destination / f'{name}.log').open('w', encoding='utf-8')
            streams.append(stream)
            process = subprocess.Popen(['node', '--input-type=module', '-e', bootstrap],
                cwd=app / 'backend', env=child_env, stdout=stream, stderr=subprocess.STDOUT,
                start_new_session=True)
            processes.append(process)
            service_ports.append(port)
            wait_ready(process, f'http://127.0.0.1:{port}', args.startup_timeout)
        # A separate package keeps independent tests away from generated tests/tdd.
        shutil.copytree(tests, destination / 'tests')
        write_json(destination / 'package.json', {'name': 'rex-independent-evaluation',
            'private': True, 'type': 'module',
            'devDependencies': {'@playwright/test': '1.57.0', '@types/node': '22.18.0'}})
        config = PLAYWRIGHT_CONFIG.replace('__REX_BASE_URL__', json.dumps(url)).replace(
            '__REX_RUN_ID__', json.dumps(run_id)).replace(
            '__REX_TIMEZONE__', json.dumps(environment['REX_TEST_TIMEZONE']))
        (destination / 'playwright.config.ts').write_text(config, encoding='utf-8')
        checked(['npm', 'install', '--no-audit', '--no-fund', '--workspaces=false'], destination, console, environment)
        browser = ['npm', 'exec', '--', 'playwright', 'install']
        if args.with_deps:
            browser.append('--with-deps')
        checked(browser + ['chromium'], destination, console, environment)
        checked(['npm', 'exec', '--', 'playwright', 'test', '--list'], destination, console, environment)
        (destination / 'report.json').unlink(missing_ok=True)
        code = run(['npm', 'exec', '--', 'playwright', 'test'], destination, console, environment)
        report = read_json(destination / 'report.json')
        if not report or not report.get('stats'):
            raise ValueError('Playwright did not produce report.json with test statistics.')
        stats = report['stats']
        total = sum(stats.get(key, 0) for key in ('expected', 'unexpected', 'flaky', 'skipped'))
        metadata.update(status='PASSED' if code == 0 and total > 0 and stats.get('skipped', 0) == 0 else 'FAILED',
                        exit_code=code, stats=stats, total=total,
                        pass_rate=(stats.get('expected', 0) / total if total else None))
        return 0 if metadata['status'] == 'PASSED' else (code or 1)
    except BaseException as exc:
        metadata.update(status='INTERRUPTED' if isinstance(exc, KeyboardInterrupt) else 'ERROR', error=str(exc))
        raise
    finally:
        for process in reversed(processes):
            stop(process)
        for stream in streams:
            stream.close()
        metadata.update(finished_at=now(), duration_seconds=round(time.monotonic() - started, 3),
                        services_stopped=all(not group_exists(process) for process in processes),
                        service_ports=service_ports)
        write_json(destination / 'summary.json', metadata)
        print(f'Evaluation record: {destination / "summary.json"}')
        print(f'HTML report: {destination / "playwright-report/index.html"}')


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest='command', required=True)
    for name, function in (('generate', generate), ('batch', batch)):
        sub = commands.add_parser(name, help='Generate only; independent evaluation is a separate command.')
        sub.set_defaults(function=function)
        sub.add_argument('--model', choices=ALIASES, required=True)
        sub.add_argument('--benchmark-root', default=DEFAULT_BENCHMARK,
                         help='Benchmark root with input/<task>/ and test/<task>/; defaults to benchmark/.')
        sub.add_argument('--env-file', help='Override .env.<model>; the selected absolute path is exported as ARC_ENV_FILE.')
        sub.add_argument('--run-id', type=label, default='run1')
        sub.add_argument('--output-root', default=ROOT / 'workspace')
        sub.add_argument('--records-root', default=ROOT / 'reproduction')
        if name == 'generate':
            sub.add_argument('--task', choices=TASKS, required=True)
            sub.add_argument('--output', help='Override the generated application directory.')
            sub.add_argument('--port', type=port_number, help='Override the fixed model/task port.')
        else:
            sub.add_argument('--tasks', help='Comma-separated subset; default: all six tasks.')
            sub.add_argument('--base-port', type=port_number, help='Override the model base; task ports use base + 10 * canonical task index.')
    sub = commands.add_parser('test', help='Deploy and independently test exactly one generated application.')
    sub.set_defaults(function=evaluate)
    sub.add_argument('--task', choices=TASKS, required=True)
    sub.add_argument('--app', required=True)
    sub.add_argument('--model', choices=ALIASES, help='Port selection fallback for applications without a generation record.')
    sub.add_argument('--port', type=port_number, help='Override the recorded/model-task port; also sets the test URL.')
    sub.add_argument('--benchmark-root', default=DEFAULT_BENCHMARK,
                         help='Benchmark root with input/<task>/ and test/<task>/; defaults to benchmark/.')
    sub.add_argument('--tests-dir', help='Override the bundled task tests with another local independent tests/ directory.')
    sub.add_argument('--eval-id', type=label, help='Default: UTC timestamp plus unique suffix.')
    sub.add_argument('--startup-timeout', type=port_number, default=60, help='Startup deadline in seconds.')
    sub.add_argument('--with-deps', action='store_true', help='Install Playwright Linux system dependencies.')
    return cli


def main():
    def terminate(_signum, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, terminate)
    args = parser().parse_args()
    try:
        return args.function(args)
    except KeyboardInterrupt:
        print('Interrupted; child services stopped and available records retained.', file=sys.stderr)
        return 130
    except (ValueError, OSError, ImportError) as exc:
        print(f'Reproduction failed: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
