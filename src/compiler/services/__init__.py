"""Compiler service boundaries."""

from .model import (
    CONTEXT_SEGMENTS_KEY,
    Model,
    ModelConfigurationError,
    StructuredModel,
    completion_request_kwargs,
    describe_model_error,
    is_deepseek_model,
    response_format_unavailable,
)

__all__ = [
    "CONTEXT_SEGMENTS_KEY",
    "Model",
    "ModelConfigurationError",
    "StructuredModel",
    "completion_request_kwargs",
    "describe_model_error",
    "is_deepseek_model",
    "response_format_unavailable",
]
