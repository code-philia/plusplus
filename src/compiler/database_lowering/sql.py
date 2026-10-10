"""SQL rendering primitives; all dialect choices live in lowering strategies."""
from __future__ import annotations

import math
import re
from typing import Any


def identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[a-z][a-z0-9_]*', value):
        raise ValueError(f'Invalid database identifier: {value!r}')
    return '"' + value + '"'


def literal(value: Any) -> str:
    if value is None:
        return 'NULL'
    if isinstance(value, bool):
        return '1' if value else '0'
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError('SQL numeric literals must be finite')
        return str(value)
    if not isinstance(value, str):
        raise ValueError(f'Unsupported SQL literal: {value!r}')
    if '\x00' in value:
        return "CAST(X'" + value.encode('utf-8').hex() + "' AS TEXT)"
    return "'" + value.replace("'", "''") + "'"


def constraint_sql(constraint: dict[str, Any]) -> str:
    columns = ', '.join(identifier(value) for value in constraint['columns'])
    prefix = f'CONSTRAINT {identifier(constraint["name"])} '
    kind = constraint['kind']
    if kind == 'PRIMARY_KEY':
        return prefix + f'PRIMARY KEY ({columns})'
    if kind == 'UNIQUE':
        if constraint.get('comparison') == 'case_insensitive':
            columns = ', '.join(identifier(value) + ' COLLATE NOCASE' for value in constraint['columns'])
        return prefix + f'UNIQUE ({columns})'
    if kind == 'FOREIGN_KEY':
        if constraint['on_delete'] != 'RESTRICT' or constraint['on_update'] != 'RESTRICT':
            raise ValueError('Unsupported foreign key action')
        targets = ', '.join(identifier(value) for value in constraint['referenced_columns'])
        return prefix + f'FOREIGN KEY ({columns}) REFERENCES {identifier(constraint["referenced_table"])} ({targets}) ON DELETE RESTRICT ON UPDATE RESTRICT'
    raise ValueError(f'Unsupported constraint: {kind!r}')
