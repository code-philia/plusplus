"""Write only the current stage's files; refuse to overwrite edited generated files."""
import hashlib
from pathlib import Path
from utils.jsonio import read_json, write_json_atomic


def digest(content):
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def write_stage(output, stage, plan, files, *, bootstrap_files=()):
    output = Path(output).resolve()
    artifact = output / '.arc/lowering' / stage
    previous = read_json(artifact / 'manifest.json', {})
    owned = previous.get('files', {})
    # Validate the entire write set before changing any source file.
    for relative, content in files.items():
        path = (output / relative).resolve()
        if not path.is_relative_to(output) or '/src/' not in '/' + relative:
            raise ValueError(f'Invalid generated source path: {relative}')
        if path.exists():
            current = path.read_text(encoding='utf-8')
            if current != content and owned.get(relative) != digest(current):
                if relative not in bootstrap_files or relative in owned:
                    raise ValueError(f'Refusing to overwrite a file not owned by this stage or edited after lowering: {relative}')
    for relative, content in files.items():
        path = output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    # Remove only unchanged files owned by the preceding run of this same stage.
    retained = []
    for relative, old_digest in owned.items():
        if relative in files:
            continue
        path = (output / relative).resolve()
        if not path.is_relative_to(output):
            raise ValueError(f'Invalid previous manifest path: {relative}')
        if path.is_file():
            if digest(path.read_text(encoding='utf-8')) == old_digest:
                path.unlink()
            else:
                retained.append(relative)
    plan['retained_modified_files'] = retained
    plan['files'] = sorted(files)
    write_json_atomic(artifact / 'plan.json', plan)
    write_json_atomic(artifact / 'manifest.json', {'files': {key: digest(value) for key, value in files.items()}})
    write_json_atomic(artifact / 'report.json', {'status': 'GENERATED', 'generated_files': len(files),
        'retained_modified_files': retained, 'validation': 'Source generation completed; run project typecheck/build.'})
    return plan
