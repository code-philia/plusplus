"""Path-indexed image evidence; analysis success does not mark a page tree parsed."""
import base64
import hashlib
import mimetypes
from pathlib import Path


def image_evidence(reference, reference_root):
    if reference_root is None:
        raise ValueError('reference_root is required to load visual references')
    root = Path(reference_root).resolve()
    path = (root / reference.replace('\\', '/')).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f'Visual reference must stay inside reference_root: {reference}')
    content = path.read_bytes()
    mime = mimetypes.guess_type(path.name)[0]
    if mime not in {'image/png', 'image/jpeg', 'image/webp', 'image/gif'}:
        raise ValueError(f'Unsupported visual reference image: {reference}')
    return hashlib.sha256(content).hexdigest(), f'data:{mime};base64,' + base64.b64encode(content).decode('ascii')
