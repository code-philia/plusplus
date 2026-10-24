from dataclasses import dataclass

from compiler.lowering.workflow import LoweringInput, LoweringOutput, LoweringPolicy, LoweringWorkflow
from compiler.stages import Stage
from .stage import FrontendLoweringPass


@dataclass(frozen=True, slots=True)
class FrontendLoweringInput(LoweringInput):
    pass


@dataclass(frozen=True, slots=True)
class FrontendLoweringOutput(LoweringOutput):
    pass


class FrontendLoweringPolicy(LoweringPolicy):
    key = 'frontend'
    design_filename = 'frontend_ir.json'

    def render(self, design):
        return self.lowerer.compile(self.inputs.requirements, design, self.inputs.output_root)


class FrontendLoweringStage(Stage[FrontendLoweringOutput]):
    def __init__(self, inputs: FrontendLoweringInput, model, budgets, *, runner=None,
                 component_depth=2, min_nodes=4):
        super().__init__('frontend_lowering', runner=runner)
        self.inputs, self.model, self.budgets = inputs, model, budgets
        self.component_depth, self.min_nodes = component_depth, min_nodes

    async def _execute(self):
        policy = FrontendLoweringPolicy(self.inputs,
            FrontendLoweringPass(component_depth=self.component_depth, min_nodes=self.min_nodes))
        return LoweringWorkflow(self.inputs, policy, self.model, self.budgets).run(FrontendLoweringOutput)
