from .base import FrontendDesignPass, FrontendPassSpec


class InferDataPass(FrontendDesignPass):
    spec = FrontendPassSpec(name="infer_data", phase="data",
                            input_level="component", output_level="data")


__all__ = ["InferDataPass"]
