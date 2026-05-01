// =============================================================================
// Drizzle ORM Database Client (SERVER-ONLY)
// =============================================================================
// Creates a singleton Postgres connection via the `postgres` driver.
// Only initialised when DATABASE_URL is set — otherwise the app runs on mock data.
//
// This module must NEVER be imported from client components.
// The `server-only` guard will throw a build error if it is.
// =============================================================================

import "server-only";
import { drizzle } from "drizzle-orm/postgres-js";
import postgres from "postgres";
import * as schema from "./schema";

// Cache the connection so we don't create a new pool on every import
let _db: ReturnType<typeof drizzle<typeof schema>> | null = null;

/**
 * Returns the Drizzle database client, or null if DATABASE_URL is not configured.
 * Safe to call from both server components and API routes.
 */
export function getDb() {
  if (_db) return _db;

  const url = process.env.DATABASE_URL;
  if (!url) return null;

  const client = postgres(url, {
    max: 10,               // connection pool size
    idle_timeout: 20,      // seconds
    connect_timeout: 10,   // seconds
  });

  _db = drizzle(client, { schema });
  return _db;
}

/**
 * Returns true if a database connection is available.
 */
export function hasDatabase(): boolean {
  return !!process.env.DATABASE_URL;
}

// Re-export schema for convenience
export { schema };
