from .base import BackendDesignPass, BackendPassSpec


class BuildAPIPass(BackendDesignPass):
    spec = BackendPassSpec(name="build_api", phase="requirement_api",
                           input_level="requirement", output_level="api")

    def run(self, module, context=None):
        context = super().run(module, context)
        api_ids = sorted(item.id for item in module.modules_of_kind("API"))
        context.results[self.spec.name].update({"module_ids": api_ids, "count": len(api_ids)})
        return context


__all__ = ["BuildAPIPass"]
