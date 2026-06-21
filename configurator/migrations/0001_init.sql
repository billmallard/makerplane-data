-- Accounts + design storage for the pyEfis configuration manager.
-- See ../../docs/system_designer.md for the data model rationale.

CREATE TABLE IF NOT EXISTS users (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  email       TEXT NOT NULL UNIQUE,
  name        TEXT,
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  last_login  TEXT
);

-- One row per linked sign-in method (google sub, or the email address itself
-- for magic-link). Lets a user link several providers to one account.
CREATE TABLE IF NOT EXISTS identities (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  provider         TEXT NOT NULL,            -- 'google' | 'email'
  provider_subject TEXT NOT NULL,            -- google 'sub', or the email
  created_at       TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(provider, provider_subject)
);

-- An "aircraft" / system design owned by a user.
CREATE TABLE IF NOT EXISTS projects (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name        TEXT NOT NULL,
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- A device within a project (pyefis today; more hardware later). Paired to the
-- aircraft by entering a claim_code on the device, which is exchanged for a
-- long-lived device token (only its hash is stored).
CREATE TABLE IF NOT EXISTS devices (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id        INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  kind              TEXT NOT NULL DEFAULT 'pyefis',
  name              TEXT NOT NULL,
  width             INTEGER,
  height            INTEGER,
  claim_code        TEXT,                    -- short pairing code, NULL once claimed
  device_token_hash TEXT,                    -- sha256 of the long-lived device token
  claimed_at        TEXT,
  last_pull_at      TEXT,
  created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Versioned screen-layout configs for a device; the YAML blob lives in R2.
CREATE TABLE IF NOT EXISTS configs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id   INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
  version     INTEGER NOT NULL DEFAULT 1,
  yaml_r2_key TEXT NOT NULL,
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_projects_user   ON projects(user_id);
CREATE INDEX IF NOT EXISTS idx_devices_project ON devices(project_id);
CREATE INDEX IF NOT EXISTS idx_devices_token   ON devices(device_token_hash);
CREATE INDEX IF NOT EXISTS idx_configs_device  ON configs(device_id);
