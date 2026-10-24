"""Frontend design context presentation, owned by the calling stage."""
from model.call_context import CallMetadata, ContextGroup, ContextLayout


FRONTEND = ContextLayout((
    ContextGroup('Current task', '', frozenset(('current_task',))),
    ContextGroup('Selected page and UI tree', '',
        frozenset(('app', 'current_page', 'loaded_ui_ids', 'page_mode', 'selected_ui_tree'))),
    ContextGroup('Eligible event UI nodes', '', frozenset(('event_ui_candidates',))),
    ContextGroup('Page state comparison', '', frozenset(('baseline_page_states',))),
    ContextGroup('Global UI summary', '', frozenset(('ui_tree_summary',))),
    ContextGroup('Backend API contracts', '', frozenset(('api_contracts',))),
    ContextGroup('Existing state and interaction contracts', '',
        frozenset(('existing_data', 'existing_events', 'handler_name_index', 'reuse_handlers'))),
    ContextGroup('Type definitions', '', frozenset(('existing_types', 'type_name_index'))),
    ContextGroup('Correction feedback', '', frozenset(('previous_output', 'validation_feedback'))),
))


def frontend_metadata(phase):
    return CallMetadata('frontend_design_' + phase, FRONTEND, log_scope='frontend_design')
