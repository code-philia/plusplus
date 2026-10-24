"""Gate repair presentation; unrelated to provider schema identifiers."""
from compiler.context.presentation import DEFAULT_GROUPS, DEFAULT_LAYOUT
from model.call_context import CallMetadata, ContextGroup, ContextLayout


OVERRIDES = {
    'Current working region': ('Relevant backend and frontend design',
        'Use actual positional signatures, page composition and state contracts. Complete this requirement and preserve other requirements sharing its files.'),
    'Read and edit scope': ('Per-invocation file permissions',
        'editable_files keys are the complete write allowlist. file_catalog is for discovery only; request_files loads missing context and the compiler rebuilds permissions before edits.'),
}
REPAIR_LAYOUT = ContextLayout(tuple(
    ContextGroup(*OVERRIDES.get(group.name, (group.name, group.purpose)), group.keys)
    for group in DEFAULT_GROUPS), DEFAULT_LAYOUT.task,
    DEFAULT_LAYOUT.remaining_name, DEFAULT_LAYOUT.remaining_purpose)


def repair_metadata():
    return CallMetadata('stage_repair', REPAIR_LAYOUT, log_scope='stage_repair')
