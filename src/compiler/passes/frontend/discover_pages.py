from .base import FrontendDesignPass, FrontendPassSpec


class DiscoverPagesPass(FrontendDesignPass):
    spec = FrontendPassSpec(name="discover_pages", phase="ui",
                            input_level="requirement", output_level="ui")


__all__ = ["DiscoverPagesPass"]
