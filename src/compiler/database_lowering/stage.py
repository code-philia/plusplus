"""Minimal table initialization and connection lowering for the initialized SQLite stack."""
import json
from compiler.ir.database import table_constraints
from compiler.lowering.files import write_stage
from .runtime import runtime_sources, SEED_PATH


def quote(value):
    return '"' + value.replace('"', '""') + '"'


def literal(value):
    if isinstance(value, bool):
        return '1' if value else '0'
    if isinstance(value, (float, int)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def create_statement(table):
    parts = []
    for column in table['columns']:
        dtype = {'string': 'TEXT', 'uuid': 'TEXT', 'date': 'TEXT', 'datetime': 'TEXT', 'json': 'TEXT',
                 'integer': 'INTEGER', 'boolean': 'INTEGER', 'number': 'REAL'}[column['type']]
        line = quote(column['name']) + ' ' + dtype + ('' if column['nullable'] else ' NOT NULL')
        default = column.get('default')
        if default:
            value = default['value']
            if default['kind'] == 'json_literal' and not isinstance(value, str):
                value = json.dumps(value, ensure_ascii=False)
            line += ' DEFAULT ' + literal(value)
        parts.append(line)
    for item in table_constraints(table):
        kind = item['kind']
        columns = ', '.join(quote(name) for name in item['columns'])
        if kind == 'CHECK':
            continue
        if kind == 'PRIMARY_KEY':
            clause = 'PRIMARY KEY (' + columns + ')'
        elif kind == 'UNIQUE':
            if item.get('comparison') == 'case_insensitive':
                columns = ', '.join(quote(name) + ' COLLATE NOCASE' for name in item['columns'])
            clause = 'UNIQUE (' + columns + ')'
        elif kind == 'FOREIGN_KEY':
            clause = ('FOREIGN KEY (' + columns + ') REFERENCES ' + quote(item['referenced_table'])
                      + ' (' + ', '.join(quote(name) for name in item['referenced_columns'])
                      + ') ON DELETE RESTRICT ON UPDATE RESTRICT')
        else:
            raise ValueError('Unsupported table constraint ' + kind)
        parts.append('CONSTRAINT ' + quote(item['name']) + ' ' + clause)
    return 'CREATE TABLE IF NOT EXISTS ' + quote(table['name']) + ' (\n  ' + ',\n  '.join(parts) + '\n);'


class DatabaseLoweringPass:
    def compile(self, schema, output_dir, *, dry_run=False):
        if schema.get('ir_kind') != 'database_schema' or schema.get('status') != 'RESOLVED':
            raise ValueError('Database lowering requires a RESOLVED SchemaIR')
        files = runtime_sources([create_statement(table) for table in schema['tables']])
        # The seed is cumulative implementation code, not a fresh placeholder on every lowering.
        from pathlib import Path
        seed = Path(output_dir) / SEED_PATH
        if seed.is_file():
            files[SEED_PATH] = seed.read_text(encoding='utf-8')
        plan = {'ir_kind': 'database_lowering', 'dialect': 'sqlite',
                'tables': [{'name': table['name'], 'reqs': table['requirement_ids'],
                            'file_path': 'backend/src/db/initialize.ts',
                            'columns': [{'name': row['name'], 'reqs': row['requirement_ids']} for row in table['columns']]}
                           for table in schema['tables']],
                'shared_modules': [{'id': 'SYSTEM.DB_CLIENT', 'name': 'Database connection', 'file_path': 'backend/src/db/client.ts'},
                    {'id': 'SYSTEM.DB_SCHEMA', 'name': 'Database table initialization', 'file_path': 'backend/src/db/initialize.ts'},
                    {'id': 'SYSTEM.SEED', 'name': 'Startup reset and cumulative seed', 'file_path': SEED_PATH}]}
        if dry_run:
            return plan, files
        return write_stage(output_dir, 'database', plan, files,
                           bootstrap_files={'backend/src/db/client.ts', 'backend/src/db/initialize.ts'})
