from dataclasses import dataclass

from compiler.lowering.workflow import LoweringInput, LoweringOutput, LoweringPolicy, LoweringWorkflow
from compiler.stages import Stage
from .stage import DatabaseLoweringPass


@dataclass(frozen=True, slots=True)
class DatabaseLoweringInput(LoweringInput):
    pass


@dataclass(frozen=True, slots=True)
class DatabaseLoweringOutput(LoweringOutput):
    pass


class DatabaseLoweringPolicy(LoweringPolicy):
    key = 'database'
    design_filename = 'schema.json'
    persist_effective = False

    def render(self, design):
        return self.lowerer.compile(design, self.inputs.output_root)


class DatabaseLoweringStage(Stage[DatabaseLoweringOutput]):
    def __init__(self, inputs: DatabaseLoweringInput, model, budgets, *, runner=None):
        super().__init__('database_lowering', runner=runner)
        self.inputs, self.model, self.budgets = inputs, model, budgets

    async def _execute(self):
        policy = DatabaseLoweringPolicy(self.inputs, DatabaseLoweringPass())
        return LoweringWorkflow(self.inputs, policy, self.model, self.budgets).run(DatabaseLoweringOutput)
