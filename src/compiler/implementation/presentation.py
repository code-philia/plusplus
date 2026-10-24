"""Implementation context presentation, owned by the calling stage."""
from model.call_context import CallMetadata, ContextGroup, ContextLayout


IMPLEMENTATION = ContextLayout((
    ContextGroup('Current task', '', frozenset(('current_task',))),
    ContextGroup('Current module contracts and types', '', frozenset(('current_contracts', 'types'))),
    ContextGroup('Always loaded requirement files', '', frozenset(('always_loaded_files',))),
    ContextGroup('Working context and latest read results', '', frozenset(('context_state',))),
    ContextGroup('Editable files with current contents', '', frozenset(('editable_files',))),
    ContextGroup('Read-only source and phase-protected tests', '', frozenset(('read_only_files',))),
    ContextGroup('All shared module summaries', '', frozenset(('shared_catalog',))),
    ContextGroup('Related module discovery', '', frozenset(('related_catalog',))),
    ContextGroup('Discoverable file index', '', frozenset(('file_index',))),
    ContextGroup('Acceptance test targets', '', frozenset(('test_targets',))),
    ContextGroup('Observed gates, failing test context and correction feedback', '',
        frozenset(('failure_context', 'gate_results', 'previous_output', 'validation_feedback'))),
))


def implementation_metadata(phase):
    return CallMetadata('tdd_' + phase, IMPLEMENTATION, log_scope='implementation')
