"""Apply explicit metadata without changing the model provider protocol."""
from model.call_context import call_scope


def invoke_model(model, metadata, *, method='generate_json', **request):
    with call_scope(metadata):
        return getattr(model, method)(**request)
