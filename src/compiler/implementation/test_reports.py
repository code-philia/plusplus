"""Read fixed test-runner reports and classify evidence without executing commands."""
import json
from pathlib import Path


def read_test_report(root, result, path, runner, expected_files):
    """Update the command result in place; missing reports do not imply infrastructure failure."""
    try:
        report = json.loads(path.read_text(encoding='utf-8-sig'))
        failures, failed_tests, collected, executed_files = [], [], 0, set()
        timed_out = result.get('failure_kind') == 'execution_timeout'
        if runner == 'vitest':
            for suite in report.get('testResults', []):
                assertions = suite.get('assertionResults', [])
                collected += len(assertions)
                if assertions:
                    executed_files.add(suite['name'])
                failures.extend(message for test in assertions if test['status'] == 'failed'
                                for message in test.get('failureMessages', []))
                for test in assertions:
                    if test['status'] == 'failed':
                        failed_tests.append({'file': suite['name'],
                            'name': test.get('fullName') or test.get('title', ''),
                            'location': test.get('location'), 'errors': test.get('failureMessages', [])})
                if suite.get('status') == 'failed' and not any(test['status'] == 'failed' for test in assertions):
                    message = suite.get('message', '')
                    failures.append(message)
                    failed_tests.append({'file': suite['name'], 'name': 'suite/setup', 'errors': [message]})
        else:
            def visit(suites):
                nonlocal collected, timed_out
                for suite in suites:
                    for spec in suite.get('specs', []):
                        collected += len(spec.get('tests', []))
                        executed_files.add(spec['file'])
                        for test in spec.get('tests', []):
                            for run in test.get('results', []):
                                if run['status'] in {'failed', 'timedOut', 'interrupted'}:
                                    timed_out = timed_out or run['status'] == 'timedOut'
                                    errors = run.get('errors', []) or [{'message':
                                        f"E2E case {spec.get('title', '')} ended with status {run['status']}."}]
                                    failures.extend(error.get('message', '') for error in errors)
                                    reported_file = Path(spec['file'])
                                    if not reported_file.is_absolute():
                                        if spec['file'].replace('\\', '/').startswith('tests/'):
                                            reported_file = root / reported_file
                                        else:
                                            test_root = Path(report.get('config', {}).get('rootDir') or 'tests/e2e')
                                            reported_file = root / test_root / reported_file
                                    failed_tests.append({'file': str(reported_file.resolve()), 'name': spec.get('title', ''),
                                        'location': {'line': spec.get('line'), 'column': spec.get('column')},
                                        'status': run['status'], 'errors': errors})
                    visit(suite.get('suites', []))
            visit(report.get('suites', []))
        report_errors = [str(error.get('message', error)) if isinstance(error, dict) else str(error)
                         for error in report.get('errors', [])]
        failures += report_errors
        if runner == 'playwright':
            timed_out = timed_out or any('timeout' in message.lower() or 'timed out' in message.lower()
                                         for message in report_errors)
        infrastructure = any(any(marker in message.lower() for marker in
            ('executable doesn\'t exist', 'browser was not found', 'failed to launch')) for message in failures)
        test_code = any(any(marker in message.lower() for marker in
            ('syntaxerror', 'transform failed', 'no tests found', 'is not a function',
             'referenceerror', 'cannot find module', 'failed to load')) for message in failures)
        kind = result.get('failure_kind') or ('infrastructure' if infrastructure else
                                             'execution_timeout' if timed_out else
                                             'test_code' if test_code else 'behavior')
        if timed_out:
            result['status'] = 'FAILED'
        if result['status'] != 'PASSED':
            result['failure_kind'] = kind
        result['test_evidence'] = {'collected': collected, 'executed_files': len(executed_files),
                                  'failures': failures, 'failed_tests': failed_tests,
                                  'behavior_failure': bool(failures) and kind == 'behavior'}
        if collected == 0 or len(executed_files) < expected_files:
            result['status'] = 'FAILED'
            result['failure_kind'] = result.get('failure_kind') or 'test_code'
            result.setdefault('error', 'Acceptance tests were not discovered for every selected file')
            result['test_evidence']['behavior_failure'] = False
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result['status'] = 'FAILED'
        # Missing reporter output alone is not proof that the environment is
        # broken: generated code can crash or hang before producing it.
        result.setdefault('failure_kind', 'test_execution')
        result['report_error'] = 'Test report unavailable or malformed: ' + type(exc).__name__
        result.setdefault('error', result['report_error'])
