"""Backend design context presentation, owned by the calling stage."""
from model.call_context import CallMetadata, ContextGroup, ContextLayout


BACKEND = ContextLayout((
    ContextGroup('Current task', '', frozenset(('current_task',))),
    ContextGroup('Upstream modules', '', frozenset(('upstream_modules',))),
    ContextGroup('Enclosing API contracts and transport effects', '', frozenset(('upstream_api_contracts',))),
    ContextGroup('Reusable contracts', '', frozenset(('reference_catalog', 'reuse_candidates'))),
    ContextGroup('Type definitions', '', frozenset(('existing_types',))),
    ContextGroup('Global conflict indexes', '', frozenset(('name_index', 'route_index'))),
    ContextGroup('Correction feedback', '', frozenset(('previous_output', 'validation_feedback'))),
))


def backend_metadata(kind):
    return CallMetadata('backend_' + kind.lower(), BACKEND, log_scope='backend_design')
