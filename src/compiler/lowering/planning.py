"""Stable requirement ownership and source paths shared by both lowering stages."""
import hashlib
import re
from pathlib import PurePosixPath


def slug(name):
    name = re.sub(r'([a-z0-9])([A-Z])', r'\1-\2', str(name)).lower()
    return re.sub(r'[^a-z0-9]+', '-', name).strip('-') or 'module'


def token(value):
    return hashlib.sha256(str(value).encode()).hexdigest()[:8]


def group_for(reqs, requirements):
    nodes = requirements['nodes']
    paths = []
    for rid in sorted(set(reqs)):
        if rid not in nodes:
            raise ValueError(f'Unknown lowering requirement: {rid}')
        chain, current, seen = [], nodes[rid].get('parent_id') or rid, set()
        while current in nodes:
            if current in seen:
                raise ValueError('Requirement parent cycle')
            seen.add(current)
            chain.append(current)
            current = nodes[current].get('parent_id')
        paths.append(list(reversed(chain)))
    if not paths:
        return requirements['root_id']
    common = requirements['root_id']
    for entries in zip(*paths):
        if len(set(entries)) != 1:
            break
        common = entries[0]
    return common


def group_directory(key, requirements):
    node = requirements['nodes'][key]
    return slug(key) + '-' + token(key + ':' + node.get('name', ''))


def relative_import(source, target, extension='.js'):
    import posixpath
    target = str(PurePosixPath(target).with_suffix(extension))
    path = posixpath.relpath(target, str(PurePosixPath(source).parent))
    return path if path.startswith('.') else './' + path
