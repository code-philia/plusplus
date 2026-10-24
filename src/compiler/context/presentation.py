"""Optional generic grouping vocabulary; business-specific layouts need not use it."""
from model.call_context import ContextGroup, ContextLayout


DEFAULT_GROUPS = tuple(ContextGroup(name, purpose, frozenset(keys))
    for name, purpose, keys in (('Established contracts',
  'These records define existing interfaces and types. Preserve them according to the stage '
  'instructions.',
  ('api_contracts', 'backend_ref', 'database_schema', 'layers', 'schema_ref', 'schema_slice',
   'source_targets', 'types')),
 ('Current task',
  'Implement or design only the current requirement. Parent/related requirements supply '
  'context rather than extra tasks.',
  ('focus_entity', 'implementation_mode', 'iteration', 'node_role', 'reference_id',
   'requirement', 'requirement_id', 'scenario_ids', 'test_layer')),
 ('Current working region',
  'Use these existing records as the starting point; preserve unrelated behavior and stable '
  'IDs.',
  ('analysis', 'backend_subgraph', 'baseline_page_states', 'declared_pages', 'existing',
   'existing_entities', 'frontend_subgraph', 'missing_pages', 'own_field_contributions',
   'page_context_mode', 'page_ids', 'page_matches', 'related_data', 'related_entities',
   'requirement_links', 'root_ui_id')),
 ('Reference and discovery',
  'Summaries/catalogs locate records; they do not grant edit permission. Visual evidence '
  'informs presentation, not additional business scope.',
  ('aliases', 'ancestor_context', 'api_catalog', 'available_api_ids', 'byte_size',
   'child_requirement_count', 'child_requirements', 'dependency_requirements', 'er_graph',
   'function_catalog', 'media_type', 'path', 'prerequisite_flows', 'preserve_requirements',
   'related_requirements', 'requirement_catalog', 'requirement_ids', 'reuse_context',
   'schema_index', 'sha256', 'shared_requirements', 'source_path', 'structural_ui',
   'ui_tree_summary', 'visual_references')),
 ('Read and edit scope',
  'The stage instructions and explicit allowlists control modifications. Discoverable files '
  'and summaries are not automatically editable; request unloaded records first.',
  ('context_budget_chars', 'context_requests_remaining', 'direct_editable_files',
   'file_catalog', 'file_scope', 'loaded_ui_ids', 'new_id_prefix', 'omitted_ids')),
 ('Editable source',
  'Only keys in editable_files are the file write allowlist for this invocation. Use the exact '
  'current source for replacements.',
  ('editable_files',)),
 ('Read-only source and evidence',
  'Read these files to understand contracts and behavior. They are not editable unless the '
  'stage explicitly authorizes them. Diagnosis itself is read-only.',
  ('related_files', 'source_files', 'test_files')),
 ('Failures, gaps and feedback',
  'Use execution results as observed evidence, not expected behavior. Correct rejected '
  'attempts and preserve requirements/public contracts.',
  ('agent_validation_feedback', 'architecture_feedback', 'history', 'implementation_feedback',
   'pending_capabilities', 'previous_validation_errors', 'prior_task_failures', 'test_analysis',
   'test_result', 'validation_feedback'))))

DEFAULT_LAYOUT = ContextLayout(DEFAULT_GROUPS, 'Complete the task defined by the system instructions.',
    'Additional stage context',
    'Use these additional fields according to the system instructions; they do not expand the current task or edit scope.')
