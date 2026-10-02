from .base import BackendDesignPass, BackendPassSpec


class DecomposeAPIPass(BackendDesignPass):
    spec = BackendPassSpec(name="decompose_api", phase="api_decomposition",
                           input_level="api", output_level="api")

    def run(self, module, context=None):
        context = super().run(module, context)
        api_ids = {item.id for item in module.modules_of_kind("API")}
        children = {
            item.id: sorted(child.id for child in module.modules.values() if child.parent_id == item.id)
            for item in module.modules_of_kind("API")
        }
        context.results[self.spec.name].update({
            "module_ids": sorted(api_ids),
            "children": children,
        })
        return context


__all__ = ["DecomposeAPIPass"]
