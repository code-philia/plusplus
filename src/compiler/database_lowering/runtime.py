"""SQLite runtime and the one shared startup reset/seed module."""
import json

SEED_PATH = 'backend/src/db/seed.ts'
SEED_SOURCE = '''// Shared startup fixture module. Add cumulative requirement fixtures in the transaction below.
import type Database from "better-sqlite3";
import { initializeDatabase } from "./initialize.js";

/** Reset only this connection's database, then create schema and insert all accumulated fixtures. */
export function seedDatabase(connection: Database.Database): void {
  connection.pragma("foreign_keys = OFF");
  try {
    connection.transaction(() => {
      const tables = connection.prepare("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'").all() as Array<{ name: string }>;
      for (const { name } of tables) connection.exec('DROP TABLE IF EXISTS "' + name.replaceAll('"', '""') + '"');
    })();
  } finally {
    connection.pragma("foreign_keys = ON");
  }
  initializeDatabase(connection);
  connection.transaction(() => {
    // Add INSERT statements here. Preserve fixtures accumulated for earlier requirements.
    // Use the application's actual password/hash encoding; do not create a logged-in session
    // unless the requirement explicitly states one already exists.
  })();
}
'''


def runtime_sources(statements):
    initialize = ('import type Database from "better-sqlite3";\n'
                  + 'export const schemaInitializationStatements = ' + json.dumps(statements, ensure_ascii=False, indent=2) + ' as const;\n'
                  + 'export function initializeDatabase(connection: Database.Database): void {\n'
                  + '  connection.pragma("foreign_keys = ON");\n'
                  + '  connection.transaction(() => {\n'
                  + '    for (const statement of schemaInitializationStatements) connection.exec(statement);\n'
                  + '  })();\n}\n')
    client = '''import Database from "better-sqlite3";
import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
const defaultDatabase = fileURLToPath(new URL("../../data/app.db", import.meta.url));
export function createDatabaseClient(url = process.env.DATABASE_URL ?? defaultDatabase) {
  if (!url.trim()) throw new Error("DATABASE_URL must not be empty");
  if (url !== ":memory:") mkdirSync(dirname(resolve(url)), { recursive: true });
  const connection = new Database(url);
  connection.pragma("foreign_keys = ON");
  return { connection, close: () => connection.close() };
}
export type DatabaseClient = ReturnType<typeof createDatabaseClient>;
export let sqliteDatabase: Database.Database;
export function bindDatabaseClient(client: DatabaseClient): void { sqliteDatabase = client.connection; }
export function schemaTransaction<T>(work: () => T): T { return sqliteDatabase.transaction(work)(); }
'''
    return {'backend/src/db/client.ts': client, 'backend/src/db/initialize.ts': initialize,
            SEED_PATH: SEED_SOURCE}
