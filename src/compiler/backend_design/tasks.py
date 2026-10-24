"""Backend subtask inputs and context construction; leaf commit stays in the pass."""
from dataclasses import dataclass
from typing import Any, Callable

from .context import retrieve, module_index


@dataclass(frozen=True, slots=True)
class BackendTaskInput:
    requirement_id: str
    candidate: dict[str, Any]
    selected: list[str]
    requirement: dict[str, Any]
    data: dict[str, Any]
    database: dict[str, Any]


@dataclass(frozen=True, slots=True)
class BackendTaskResult:
    candidate: dict[str, Any]
    selected: list[str]


class BackendDesignTask:
    kind: str

    def __init__(self, inputs: BackendTaskInput, decision: Callable):
        self.inputs, self.decision = inputs, decision

    def run(self) -> BackendTaskResult:
        inputs = self.inputs
        trial, selected, kind = inputs.candidate, inputs.selected, self.kind
        upstream = [row for row in trial['modules'] if row['id'] in selected]
        candidates = retrieve(trial, kind, inputs.requirement, inputs.data, inputs.database.get('access', {}))
        reference_ids = {row['id'] for row in candidates + upstream}
        by_id = {row['id']: row for row in trial['modules']}
        pending = list(reference_ids)
        while pending:
            for target in by_id[pending.pop()]['callees']:
                if target not in reference_ids:
                    reference_ids.add(target)
                    pending.append(target)
        references = [row for row in trial['modules'] if row['id'] in reference_ids and row['kind'] in
                      ({'FUNC', 'DB'} if kind == 'FUNC' else {'DB'} if kind == 'DB' else set())]
        ancestors = set(selected)
        pending = list(ancestors)
        while pending:
            for caller in by_id[pending.pop()]['callers']:
                if caller not in ancestors:
                    ancestors.add(caller)
                    pending.append(caller)
        payload = {'current_task': {'requirement': inputs.requirement, 'data': inputs.data},
                   'upstream_modules': upstream, 'reuse_candidates': candidates,
                   'existing_types': trial['types'], 'reference_catalog': references,
                   'module_index': module_index(trial['modules'])}
        if kind == 'DB':
            payload['upstream_api_contracts'] = [row for row in trial['modules']
                                                if row['id'] in ancestors and row['kind'] == 'API']
        candidate, selected = self.decision(kind, inputs.requirement_id, trial, selected, payload, inputs.database)
        return BackendTaskResult(candidate, selected)


class APIDesignTask(BackendDesignTask):
    kind = 'API'


class FunctionDesignTask(BackendDesignTask):
    kind = 'FUNC'


class DatabaseAccessDesignTask(BackendDesignTask):
    kind = 'DB'
