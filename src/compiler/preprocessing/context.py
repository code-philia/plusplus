"""Select requirement evidence without flattening descriptions or behavioral steps."""
import copy


def description_text(node):
    description = node.get('description', {})
    return description.get('text', '') if isinstance(description, dict) else str(description)


def requirement_evidence(node):
    return {key: copy.deepcopy(node[key]) for key in
            ('id', 'name', 'description', 'scenarios', 'pages', 'reference_context') if key in node}
