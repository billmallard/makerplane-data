// Shared types for the configurator Worker.

export interface Bindings {
  DB: D1Database;
  KV: KVNamespace;
  ASSETS: Fetcher;
  SESSION_SECRET: string;
  GOOGLE_CLIENT_ID: string;
  GOOGLE_CLIENT_SECRET: string;
  APP_URL: string;
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
