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


def predicate(raw: dict[str, Any]) -> str:
    column = identifier(raw['column'])
    op, value = raw['operator'], raw['value']
    if op in {'length_gte', 'length_lte'}:
        if type(value) is not int or value < 0:
            raise ValueError('Length predicates require a nonnegative integer')
        return f'length({column}) {">=" if op == "length_gte" else "<="} {literal(value)}'
    if op in {'gte', 'lte'}:
        if type(value) not in {int, float}:
            raise ValueError('Numeric predicates require a number')
        return f'{column} {">=" if op == "gte" else "<="} {literal(value)}'
    if op == 'in' and isinstance(value, list) and value:
        return f'{column} IN ({", ".join(literal(item) for item in value)})'
    raise ValueError(f'Unsupported CHECK predicate: {op!r}')


def constraint_sql(constraint: dict[str, Any]) -> str:
    columns = ', '.join(identifier(value) for value in constraint['columns'])
    prefix = f'CONSTRAINT {identifier(constraint["name"])} '
    kind = constraint['kind']
    if kind == 'PRIMARY_KEY':
        return prefix + f'PRIMARY KEY ({columns})'
    if kind == 'UNIQUE':
        return prefix + f'UNIQUE ({columns})'
    if kind == 'CHECK':
        return prefix + f'CHECK ({predicate(constraint["predicate"])})'
    if kind == 'FOREIGN_KEY':
        if constraint['on_delete'] != 'RESTRICT' or constraint['on_update'] != 'RESTRICT':
            raise ValueError('Unsupported foreign key action')
        targets = ', '.join(identifier(value) for value in constraint['referenced_columns'])
        return prefix + f'FOREIGN KEY ({columns}) REFERENCES {identifier(constraint["referenced_table"])} ({targets}) ON DELETE RESTRICT ON UPDATE RESTRICT'
    raise ValueError(f'Unsupported constraint: {kind!r}')
