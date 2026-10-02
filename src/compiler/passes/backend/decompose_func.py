from .base import BackendDesignPass, BackendPassSpec


class DecomposeFUNCPass(BackendDesignPass):
    spec = BackendPassSpec(name="decompose_func", phase="func_decomposition",
                           input_level="api", output_level="module")

    def run(self, module, context=None):
        context = super().run(module, context)
        functions = module.modules_of_kind("FUNC")
        context.results[self.spec.name].update({
            "module_ids": sorted(item.id for item in functions),
            "children": {
                item.id: sorted(child.id for child in module.modules.values() if child.parent_id == item.id)
                for item in functions
            },
        })
        return context


__all__ = ["DecomposeFUNCPass"]
