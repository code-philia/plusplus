"""Database decision presentation and explicit operation metadata."""
from compiler.context.presentation import DEFAULT_GROUPS, DEFAULT_LAYOUT
from model.call_context import CallMetadata, ContextGroup, ContextLayout


OVERRIDES = {'Current working region': ('Target entities and field contributions',
                            "Reuse stable entity keys. Apply only this requirement's "
                            'contributions; preserve contributions from other requirements.'),
 'Reference and discovery': ('ER graph and associated requirement evidence',
                             'Use the graph to locate related entities and avoid duplication. '
                             'Related requirements explain relationships rather than adding '
                             'tasks.')}
DATABASE = ContextLayout(tuple(ContextGroup(*OVERRIDES.get(group.name, (group.name, group.purpose)), group.keys)
    for group in DEFAULT_GROUPS), remaining_name=DEFAULT_LAYOUT.remaining_name,
    remaining_purpose=DEFAULT_LAYOUT.remaining_purpose)
DATABASE_TASKS = {'pass1_entities': 'Identify or reuse entities for the current requirement.',
 'pass2_fields': "Add the current requirement's fields to the selected entities.",
 'pass3_relationships': 'Design relationships for the focus entity using the supplied '
                        'requirements.'}


def database_metadata(phase):
    layout = ContextLayout(DATABASE.groups, DATABASE_TASKS[phase],
                           DATABASE.remaining_name, DATABASE.remaining_purpose)
    operation = {'pass1_entities': 'arc_database_entities', 'pass2_fields': 'arc_database_fields',
                 'pass3_relationships': 'arc_database_relationships'}[phase]
    return CallMetadata(operation, layout, log_scope='database_design')
