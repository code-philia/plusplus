"""Stable requirement ownership and source paths shared by both lowering stages."""
import hashlib
import re
from pathlib import PurePosixPath


def portable_stem(name):
    """Escape Windows device names without changing the exported symbol."""
    # These names are reserved even with a file extension, e.g. CON.ts.
    if re.fullmatch(r'con|prn|aux|nul|com[1-9]|lpt[1-9]', name, re.IGNORECASE):
        return '_' + name
    return name


def slug(name):
    name = re.sub(r'([a-z0-9])([A-Z])', r'\1-\2', str(name)).lower()
    return portable_stem(re.sub(r'[^a-z0-9]+', '-', name).strip('-') or 'module')


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
    name = slug(node.get('name') or key)
    duplicates = [rid for rid, row in requirements['nodes'].items()
                  if slug(row.get('name') or rid) == name]
    return name if len(duplicates) == 1 else name + '-' + slug(key)


def identifier(name, fallback='Component'):
    """Use semantic names as TypeScript exports, including valid Unicode letters."""
    words = re.findall(r'[^\W_]+', str(name), re.UNICODE)
    result = ''.join(word[0].upper() + word[1:] for word in words) or fallback
    if result[0].isdigit():
        result = fallback + result
    return result


def unique_name(name, used, *, qualifier='', fallback='Component'):
    """Disambiguate only collisions, using readable context then an ordinal."""
    base = identifier(name, fallback)
    candidate = base
    used_names = {value.casefold() for value in used}
    if candidate.casefold() in used_names and qualifier:
        candidate = identifier(qualifier, fallback) + base
    qualified = candidate
    number = 2
    while candidate.casefold() in used_names:
        candidate = qualified + str(number)
        number += 1
    used.add(candidate)
    return candidate


def relative_import(source, target, extension='.js'):
    """Handle native Windows paths as well as project-relative POSIX paths."""
    import ntpath
    import posixpath
    source = str(source).replace('\\', '/')
    target = str(PurePosixPath(str(target).replace('\\', '/')).with_suffix(extension))
    parent = str(PurePosixPath(source).parent)
    if ntpath.splitdrive(source)[0] or ntpath.splitdrive(target)[0]:
        path = ntpath.relpath(target, parent).replace('\\', '/')
    else:
        path = posixpath.relpath(target, parent)
    return path if path.startswith('.') else './' + path
