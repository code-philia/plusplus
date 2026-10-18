"""A requirement-local working set; source truth stays on disk, history in model_logs."""
import json
import os


class WorkingContext:
    def __init__(self):
        self.active = set()
        self.released = set()
        self.focus = set()
        self.recency = {}
        self.clock = 0
        self.last_operation = None
        try:
            self.max_chars = max(1, int(os.getenv('ARC_TDD_CONTEXT_FILE_CHARS', '400000')))
        except ValueError:
            self.max_chars = 400000

    def touch(self, paths):
        self.clock += 1
        for path in paths:
            self.recency[path] = self.clock

    def apply(self, request, reads, releases, pinned, call_id):
        """Reading is idempotent; releasing affects context only, never files."""
        self.active.update(reads)
        self.released.difference_update(reads)
        self.focus = set(reads)
        self.touch(reads)
        removed = releases - pinned
        self.active.difference_update(removed)
        self.focus.difference_update(removed)
        self.released.update(removed)
        self.last_operation = {
            'source_call': call_id,
            'request': {key: list(request.get(key, [])) for key in ('request_files', 'release_files')},
            'read_paths': sorted(reads), 'released_paths': sorted(removed),
            'kept_pinned_paths': sorted(releases & pinned),
        }

    def select(self, records, pinned, direct):
        """Prefer explicit recent reads; evict complete optional records, never truncate."""
        protected = pinned | self.focus
        order = sorted(records, key=lambda path: (
            path not in protected, -self.recency.get(path, 0), path not in direct, path))
        selected, omitted, size = {}, [], 0
        for path in order:
            cost = len(json.dumps(records[path], ensure_ascii=False))
            if path not in protected and size + cost > self.max_chars:
                omitted.append(path)
                continue
            selected[path] = records[path]
            size += cost
        self.active.difference_update(omitted)
        self.released.update(omitted)
        self.focus.clear()  # Explicit reads are protected for their delivery, not forever.
        metadata = {'file_chars': size, 'file_budget_chars': self.max_chars,
                    'budget_exceeded': size > self.max_chars,
                    'pinned_files': sorted(pinned & records.keys()),
                    'automatically_released_files': sorted(omitted)}
        if self.last_operation is not None:
            operation = self.last_operation
            metadata['last_operation'] = {
                'source_call': operation['source_call'], 'request': operation['request'],
                'results': [
                    {'file': path, 'status': ('loaded' if path in selected else
                                             'released' if path in self.released else 'outside_current_phase'),
                     **({'exists': selected[path]['exists']}
                        if path in selected else {})}
                    for path in operation['read_paths']],
                'released_files': operation['released_paths'],
                'kept_pinned_files': operation['kept_pinned_paths'],
            }
        return selected, metadata
