-- Aircraft Parameter Profiles (docs/canfix_configurator.md, Phase A).
-- One profile per PROJECT (the aircraft), append-only versions, one
-- active. json = {schema_version, meta:{engines,cylinders,tanks,
-- speed_unit,...}, values:{"IAS.Vne":160,...}}.
CREATE TABLE IF NOT EXISTS aircraft_profiles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  version INTEGER NOT NULL,
  json TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (project_id, version)
);
CREATE INDEX IF NOT EXISTS idx_aircraft_profiles_project
  ON aircraft_profiles(project_id, active);
