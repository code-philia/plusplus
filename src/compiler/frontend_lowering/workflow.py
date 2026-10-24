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
    def __init__(self, inputs: FrontendLoweringInput, model, budgets, *, runner=None):
        super().__init__('frontend_lowering', runner=runner)
        self.inputs, self.model, self.budgets = inputs, model, budgets

    async def _execute(self):
        policy = FrontendLoweringPolicy(self.inputs, FrontendLoweringPass())
        return LoweringWorkflow(self.inputs, policy, self.model, self.budgets).run(FrontendLoweringOutput)
