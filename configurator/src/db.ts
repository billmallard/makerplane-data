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

// --- projects (ownership-scoped) -------------------------------------------

export async function getProject(
  db: D1Database,
  userId: number,
  projectId: number,
): Promise<Record<string, unknown> | null> {
  return db
    .prepare(
      `SELECT id, name, created_at, updated_at
         FROM projects WHERE id = ?1 AND user_id = ?2`,
    )
    .bind(projectId, userId)
    .first();
}

export async function deleteProject(
  db: D1Database,
  userId: number,
  projectId: number,
): Promise<void> {
  await db
    .prepare(`DELETE FROM projects WHERE id = ?1 AND user_id = ?2`)
    .bind(projectId, userId)
    .run();
}

// --- devices (ownership via the parent project) ----------------------------

export async function listDevices(
  db: D1Database,
  userId: number,
  projectId: number,
): Promise<unknown[]> {
  const res = await db
    .prepare(
      `SELECT d.id, d.kind, d.name, d.width, d.height,
              d.claimed_at, d.last_pull_at, d.created_at,
              c.version AS latest_version, c.created_at AS latest_config_at
         FROM devices d JOIN projects p ON p.id = d.project_id
         LEFT JOIN configs c ON c.device_id = d.id
              AND c.version = (SELECT MAX(version) FROM configs WHERE device_id = d.id)
        WHERE d.project_id = ?1 AND p.user_id = ?2
        ORDER BY d.created_at`,
    )
    .bind(projectId, userId)
    .all();
  return res.results;
}

export async function createDevice(
  db: D1Database,
  userId: number,
  projectId: number,
  opts: { name: string; kind?: string; width?: number; height?: number },
): Promise<Record<string, unknown> | null> {
  const project = await getProject(db, userId, projectId);
  if (!project) return null; // not owned / not found
  return db
    .prepare(
      `INSERT INTO devices (project_id, kind, name, width, height)
         VALUES (?1, ?2, ?3, ?4, ?5)
       RETURNING id, kind, name, width, height, created_at`,
    )
    .bind(projectId, opts.kind ?? "pyefis", opts.name,
          opts.width ?? null, opts.height ?? null)
    .first();
}

export async function getDevice(
  db: D1Database,
  userId: number,
  deviceId: number,
): Promise<Record<string, unknown> | null> {
  return db
    .prepare(
      `SELECT d.id, d.project_id, d.kind, d.name, d.width, d.height
         FROM devices d JOIN projects p ON p.id = d.project_id
        WHERE d.id = ?1 AND p.user_id = ?2`,
    )
    .bind(deviceId, userId)
    .first();
}

export async function deleteDevice(
  db: D1Database,
  userId: number,
  deviceId: number,
): Promise<void> {
  await db
    .prepare(
      `DELETE FROM devices
        WHERE id = ?1
          AND project_id IN (SELECT id FROM projects WHERE user_id = ?2)`,
    )
    .bind(deviceId, userId)
    .run();
}

// --- pairing ---------------------------------------------------------------

// Record the outstanding claim code on an owned device (for display/status; the
// authoritative code + its TTL live in KV). Returns false if not owned/found.
export async function setClaimCode(
  db: D1Database,
  userId: number,
  deviceId: number,
  code: string | null,
): Promise<boolean> {
  const res = await db
    .prepare(
      `UPDATE devices SET claim_code = ?3
        WHERE id = ?1
          AND project_id IN (SELECT id FROM projects WHERE user_id = ?2)`,
    )
    .bind(deviceId, userId, code)
    .run();
  return (res.meta.changes ?? 0) > 0;
}

// Redeem a claim code (already validated via KV by the caller): bind the device
// token hash, stamp claimed_at, clear the claim code. Returns the device's
// id/name/kind for confirmation, or null if the device no longer exists.
export async function claimDevice(
  db: D1Database,
  deviceId: number,
  tokenHash: string,
): Promise<Record<string, unknown> | null> {
  return db
    .prepare(
      `UPDATE devices
          SET device_token_hash = ?2, claimed_at = datetime('now'), claim_code = NULL
        WHERE id = ?1
      RETURNING id, name, kind`,
    )
    .bind(deviceId, tokenHash)
    .first();
}

// Look up a device by its token hash (for the device-token-authed config pull),
// joined to its latest config version + R2 key (null if it has no config yet).
export async function deviceByTokenHash(
  db: D1Database,
  tokenHash: string,
): Promise<Record<string, unknown> | null> {
  return db
    .prepare(
      `SELECT d.id,
              c.version    AS version,
              c.yaml_r2_key AS yaml_r2_key
         FROM devices d
         LEFT JOIN configs c
                ON c.device_id = d.id
               AND c.version = (SELECT MAX(version) FROM configs WHERE device_id = d.id)
        WHERE d.device_token_hash = ?1`,
    )
    .bind(tokenHash)
    .first();
}

export async function touchLastPull(db: D1Database, deviceId: number): Promise<void> {
  await db
    .prepare(`UPDATE devices SET last_pull_at = datetime('now') WHERE id = ?1`)
    .bind(deviceId)
    .run();
}

// --- configs (versioned; YAML blob lives in R2) ----------------------------

export async function nextConfigVersion(
  db: D1Database,
  deviceId: number,
): Promise<number> {
  const row = await db
    .prepare(`SELECT COALESCE(MAX(version), 0) + 1 AS v FROM configs WHERE device_id = ?1`)
    .bind(deviceId)
    .first<{ v: number }>();
  return row?.v ?? 1;
}

export async function insertConfig(
  db: D1Database,
  deviceId: number,
  version: number,
  key: string,
): Promise<void> {
  await db
    .prepare(`INSERT INTO configs (device_id, version, yaml_r2_key) VALUES (?1, ?2, ?3)`)
    .bind(deviceId, version, key)
    .run();
}

export async function latestConfig(
  db: D1Database,
  userId: number,
  deviceId: number,
): Promise<Record<string, unknown> | null> {
  return db
    .prepare(
      `SELECT c.version, c.yaml_r2_key, c.created_at
         FROM configs c
         JOIN devices d ON d.id = c.device_id
         JOIN projects p ON p.id = d.project_id
        WHERE c.device_id = ?1 AND p.user_id = ?2
        ORDER BY c.version DESC LIMIT 1`,
    )
    .bind(deviceId, userId)
    .first();
}
