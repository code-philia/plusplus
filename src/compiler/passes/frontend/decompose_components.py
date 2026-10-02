from .base import FrontendDesignPass, FrontendPassSpec


class DecomposeComponentsPass(FrontendDesignPass):
    spec = FrontendPassSpec(name="decompose_components", phase="assemble",
                            input_level="ui", output_level="component")


__all__ = ["DecomposeComponentsPass"]
