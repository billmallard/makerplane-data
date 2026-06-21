// makerplane-configurator: accounts/auth + design-storage API for the pyEfis
// configuration manager. See ../../docs/system_designer.md.

import { Hono } from "hono";

import { createProject, getUser, listProjects } from "./db";
import { emailRequest, emailVerify } from "./email";
import { googleCallback, googleStart } from "./google";
import { endSession, requireUser } from "./session";
import type { Env } from "./types";

const app = new Hono<Env>();

app.get("/healthz", (c) => c.json({ service: "makerplane-configurator", ok: true }));

// ----------------------------------------------------------------------------
// Auth
// ----------------------------------------------------------------------------
app.get("/auth/google/start", googleStart);
app.get("/auth/google/callback", googleCallback);
app.post("/auth/email/request", emailRequest);
app.get("/auth/email/verify", emailVerify);
app.post("/auth/logout", (c) => {
  endSession(c);
  return c.json({ ok: true });
});

// ----------------------------------------------------------------------------
// Session-protected API (designs live here as the editor grows)
// ----------------------------------------------------------------------------
app.use("/api/*", requireUser);

app.get("/api/me", async (c) => {
  const user = await getUser(c.env.DB, c.get("userId"));
  if (!user) return c.json({ error: "not found" }, 404);
  return c.json({ user });
});

app.get("/api/projects", async (c) => {
  return c.json({ projects: await listProjects(c.env.DB, c.get("userId")) });
});

app.post("/api/projects", async (c) => {
  const body = await c.req.json<{ name?: string }>().catch(() => ({ name: undefined }));
  const name = body.name?.trim();
  if (!name) return c.json({ error: "name required" }, 400);
  return c.json({ project: await createProject(c.env.DB, c.get("userId"), name) }, 201);
});

// Everything else (/, /index.html, static files) is served from public/ via
// the ASSETS binding -- same origin as the API so the session cookie applies.
app.all("*", (c) => c.env.ASSETS.fetch(c.req.raw));

export default app;
