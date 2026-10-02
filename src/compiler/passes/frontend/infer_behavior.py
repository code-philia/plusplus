from .base import FrontendDesignPass, FrontendPassSpec


class InferBehaviorPass(FrontendDesignPass):
    spec = FrontendPassSpec(name="infer_behavior", phase="behavior",
                            input_level="data", output_level="frontend")


__all__ = ["InferBehaviorPass"]
