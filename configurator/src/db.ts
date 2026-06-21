// D1 access helpers. Designs (project blobs) will live in R2; D1 holds the
// records that tie users -> projects -> devices -> configs.

import type { User } from "./types";

export async function upsertUser(
  db: D1Database,
  email: string,
  name: string | null,
): Promise<User> {
  const row = await db
    .prepare(
      `INSERT INTO users (email, name, last_login)
         VALUES (?1, ?2, datetime('now'))
       ON CONFLICT(email) DO UPDATE SET
         name = COALESCE(excluded.name, users.name),
         last_login = datetime('now')
       RETURNING id, email, name`,
    )
    .bind(email.toLowerCase(), name)
    .first<User>();
  if (!row) throw new Error("upsertUser: no row returned");
  return row;
}

export async function linkIdentity(
  db: D1Database,
  userId: number,
  provider: string,
  subject: string,
): Promise<void> {
  await db
    .prepare(
      `INSERT INTO identities (user_id, provider, provider_subject)
         VALUES (?1, ?2, ?3)
       ON CONFLICT(provider, provider_subject) DO NOTHING`,
    )
    .bind(userId, provider, subject)
    .run();
}

export async function getUser(db: D1Database, userId: number): Promise<User | null> {
  return db
    .prepare(`SELECT id, email, name FROM users WHERE id = ?1`)
    .bind(userId)
    .first<User>();
}

export async function listProjects(db: D1Database, userId: number): Promise<unknown[]> {
  const res = await db
    .prepare(
      `SELECT id, name, created_at, updated_at
         FROM projects WHERE user_id = ?1 ORDER BY updated_at DESC`,
    )
    .bind(userId)
    .all();
  return res.results;
}

export async function createProject(
  db: D1Database,
  userId: number,
  name: string,
): Promise<unknown> {
  return db
    .prepare(
      `INSERT INTO projects (user_id, name)
         VALUES (?1, ?2)
       RETURNING id, name, created_at, updated_at`,
    )
    .bind(userId, name)
    .first();
}
