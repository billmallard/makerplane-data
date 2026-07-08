// SPDX-License-Identifier: AGPL-3.0-or-later
// Shared types for the configurator Worker.

export interface Bindings {
  DB: D1Database;
  KV: KVNamespace;
  CONFIGS: R2Bucket;
  ASSETS: Fetcher;
  SESSION_SECRET: string;
  GOOGLE_CLIENT_ID: string;
  GOOGLE_CLIENT_SECRET: string;
  APP_URL: string;
  // Environment name (docs/environments.md). Unset/"prod" -> bare R2 prefixes,
  // so an un-migrated prod deploy is byte-identical to today. dev/qa isolate by
  // prefix. Non-secret var, set per-env in wrangler.jsonc.
  ENV?: "dev" | "qa" | "prod";
  MAIL_API_KEY?: string;
  MAIL_FROM?: string;
}

export interface Variables {
  userId: number;
}

export type Env = { Bindings: Bindings; Variables: Variables };

export interface User {
  id: number;
  email: string;
  name: string | null;
}
