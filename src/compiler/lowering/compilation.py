"""Compile generated skeletons with the initialized project's compiler settings."""
import subprocess
import os
import tempfile
from pathlib import Path
from utils.encoding import utf8_environment
from utils.jsonio import read_json, write_json_atomic


def check_compilation(output, stage):
    output = Path(output).resolve()
    artifact = output / '.arc/lowering' / stage
    project_stage = 'backend' if stage == 'database' else stage
    config_name = 'tsconfig.json' if project_stage == 'backend' else 'tsconfig.app.json'
    project_config = output / project_stage / config_name
    if not project_config.is_file() and stage == 'frontend':
        project_config = output / stage / 'tsconfig.json'
    candidates = [output / project_stage / 'node_modules/typescript/bin/tsc',
                  output / 'node_modules/typescript/bin/tsc']
    compiler = next((path for path in candidates if path.is_file()), None)
    if compiler is None or not project_config.is_file():
        result = {'status': 'COMPILATION_UNAVAILABLE', 'message': 'Initialize the project tsconfig and install its TypeScript dependencies before lowering.'}
    else:
        from .planning import relative_import
        manifest = read_json(artifact / 'manifest.json', {})
        generated_files = manifest.get('files', {})
        with tempfile.TemporaryDirectory(prefix='.arc-compile-', dir=output) as directory:
            check_config = Path(directory) / 'tsconfig.json'
            write_json_atomic(check_config, {
                'extends': relative_import(str(check_config), str(project_config), '.json'),
                'compilerOptions': {'noEmit': True, 'composite': False, 'incremental': True,
                                    'tsBuildInfoFile': './skeleton.tsbuildinfo'},
                'files': [str(output / path) for path in generated_files],
                'include': [], 'exclude': [], 'references': [],
            })
            try:
                process = subprocess.run(['node', str(compiler), '-p', str(check_config)],
                                         cwd=output, capture_output=True, text=True, encoding='utf-8',
                                         errors='replace', env=utf8_environment(os.environ), timeout=60)
                result = {'status': 'COMPILED' if process.returncode == 0 else 'COMPILATION_FAILED',
                          'exit_code': process.returncode, 'output': (process.stdout + process.stderr).strip()}
            except (OSError, subprocess.TimeoutExpired) as exc:
                result = {'status': 'COMPILATION_FAILED', 'message': str(exc)}
    report = read_json(artifact / 'report.json', {})
    report['status'], report['compilation'] = result['status'], {key: value for key, value in result.items() if key != 'output'}
    if result.get('output') and result['status'] != 'COMPILED':
        report['compilation']['message'] = result['output'][:1500]
    report['validation'] = 'Generated skeleton sources checked against the initialized project settings.'
    write_json_atomic(artifact / 'report.json', report)
    return result
