from dataclasses import dataclass

from compiler.lowering.workflow import LoweringInput, LoweringOutput, LoweringPolicy, LoweringWorkflow
from compiler.stages import Stage
from .stage import BackendLoweringPass


@dataclass(frozen=True, slots=True)
class BackendLoweringInput(LoweringInput):
    pass


@dataclass(frozen=True, slots=True)
class BackendLoweringOutput(LoweringOutput):
    pass


class BackendLoweringPolicy(LoweringPolicy):
    key = 'backend'
    design_filename = 'backend_ir.json'

    def render(self, design):
        return self.lowerer.compile(self.inputs.requirements, design, self.inputs.output_root)


class BackendLoweringStage(Stage[BackendLoweringOutput]):
    def __init__(self, inputs: BackendLoweringInput, model, budgets, *, runner=None):
        super().__init__('backend_lowering', runner=runner)
        self.inputs, self.model, self.budgets = inputs, model, budgets

    async def _execute(self):
        policy = BackendLoweringPolicy(self.inputs, BackendLoweringPass())
        return LoweringWorkflow(self.inputs, policy, self.model, self.budgets).run(BackendLoweringOutput)
