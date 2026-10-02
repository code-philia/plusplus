"""Pipeline construction for the public compiler driver."""

from .compiler import ARCCompiler


def build_pipeline(*args, **kwargs) -> ARCCompiler:
    return ARCCompiler(*args, **kwargs)


__all__ = ["build_pipeline"]