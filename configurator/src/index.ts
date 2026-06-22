// makerplane-configurator: accounts/auth + design-storage API for the pyEfis
// configuration manager. See ../../docs/system_designer.md.

import { Hono } from "hono";

import {
  claimDevice,
  createDevice,
  createProject,
  deleteDevice,
  deleteProject,
  getDevice,
  getProject,
  getUser,
  insertConfig,
  latestConfig,
  listDevices,
  listProjects,
  nextConfigVersion,
  setClaimCode,
} from "./db";
import { claimCode, randomToken, sha256B64url } from "./crypto";
import { emailRequest, emailVerify } from "./email";
import { googleCallback, googleStart } from "./google";
import { endSession, requireUser } from "./session";
import type { Env } from "./types";

const PAIR_TTL_SECONDS = 15 * 60;

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

app.get("/api/projects/:id", async (c) => {
  const userId = c.get("userId");
  const projectId = Number(c.req.param("id"));
  const project = await getProject(c.env.DB, userId, projectId);
  if (!project) return c.json({ error: "not found" }, 404);
  const devices = await listDevices(c.env.DB, userId, projectId);
  return c.json({ project, devices });
});

app.delete("/api/projects/:id", async (c) => {
  await deleteProject(c.env.DB, c.get("userId"), Number(c.req.param("id")));
  return c.json({ ok: true });
});

app.post("/api/projects/:id/devices", async (c) => {
  const body = await c.req
    .json<{ name?: string; kind?: string; width?: number; height?: number }>()
    .catch(() => ({}) as { name?: string; kind?: string; width?: number; height?: number });
  if (!body.name?.trim()) return c.json({ error: "name required" }, 400);
  const device = await createDevice(c.env.DB, c.get("userId"), Number(c.req.param("id")), {
    name: body.name.trim(),
    kind: body.kind,
    width: body.width,
    height: body.height,
  });
  if (!device) return c.json({ error: "project not found" }, 404);
  return c.json({ device }, 201);
});

app.delete("/api/devices/:id", async (c) => {
  await deleteDevice(c.env.DB, c.get("userId"), Number(c.req.param("id")));
  return c.json({ ok: true });
});

// Save a new version of a device's screen config: YAML blob -> R2, row -> D1.
app.put("/api/devices/:id/config", async (c) => {
  const userId = c.get("userId");
  const deviceId = Number(c.req.param("id"));
  const device = await getDevice(c.env.DB, userId, deviceId);
  if (!device) return c.json({ error: "device not found" }, 404);
  const body = await c.req.json<{ yaml?: string }>().catch(() => ({ yaml: undefined }));
  if (typeof body.yaml !== "string" || body.yaml.length === 0) {
    return c.json({ error: "yaml required" }, 400);
  }
  const version = await nextConfigVersion(c.env.DB, deviceId);
  const key = `configs/${userId}/${deviceId}/v${version}.yaml`;
  await c.env.CONFIGS.put(key, body.yaml);
  await insertConfig(c.env.DB, deviceId, version, key);
  return c.json({ ok: true, version });
});

app.get("/api/devices/:id/config", async (c) => {
  const userId = c.get("userId");
  const latest = await latestConfig(c.env.DB, userId, Number(c.req.param("id")));
  if (!latest) return c.json({ error: "no config" }, 404);
  const obj = await c.env.CONFIGS.get(String(latest["yaml_r2_key"]));
  if (!obj) return c.json({ error: "blob missing" }, 404);
  return c.body(await obj.text(), 200, { "content-type": "application/x-yaml" });
});

// Pairing: the owner mints a short claim code (KV holds it with a TTL); the
// device redeems it for a long-lived token (see /device/pair below). #65 P1.
app.post("/api/devices/:id/pair", async (c) => {
  const userId = c.get("userId");
  const deviceId = Number(c.req.param("id"));
  const code = claimCode();
  if (!(await setClaimCode(c.env.DB, userId, deviceId, code))) {
    return c.json({ error: "device not found" }, 404);
  }
  await c.env.KV.put(`pair:${code}`, JSON.stringify({ deviceId }), {
    expirationTtl: PAIR_TTL_SECONDS,
  });
  return c.json({ claim_code: code, expires_in: PAIR_TTL_SECONDS });
});

// ----------------------------------------------------------------------------
// Device-facing endpoints (NOT session-authed — outside /api/*). Pairing is
// authorised by the claim code; later config pulls by the device token.
// ----------------------------------------------------------------------------
app.post("/device/pair", async (c) => {
  const body = await c.req.json<{ claim_code?: string }>().catch(() => ({}) as { claim_code?: string });
  const code = (body.claim_code || "").trim().toUpperCase();
  if (!code) return c.json({ error: "claim_code required" }, 400);
  const raw = await c.env.KV.get(`pair:${code}`);
  if (!raw) return c.json({ error: "invalid or expired code" }, 400);
  const { deviceId } = JSON.parse(raw) as { deviceId: number };
  const token = randomToken(32);
  const device = await claimDevice(c.env.DB, deviceId, await sha256B64url(token));
  if (!device) return c.json({ error: "device gone" }, 404);
  await c.env.KV.delete(`pair:${code}`); // single-use
  return c.json({ device_token: token, device_id: device["id"], name: device["name"] });
});

// Public editor assets (schema + thumbnails) from R2 under assets/. No auth;
// these are shared, not user data. (User configs live under configs/ and are
// only reachable through the authed /api routes above.)
app.get("/assets/*", async (c) => {
  const key = c.req.path.replace(/^\/assets\//, "assets/");
  const obj = await c.env.CONFIGS.get(key);
  if (!obj) return c.json({ error: "not found" }, 404);
  const headers: Record<string, string> = { "cache-control": "public, max-age=300" };
  const ct = obj.httpMetadata?.contentType;
  if (ct) headers["content-type"] = ct;
  return c.body(obj.body, 200, headers);
});

// Everything else (/, /index.html, static files) is served from public/ via
// the ASSETS binding -- same origin as the API so the session cookie applies.
app.all("*", (c) => c.env.ASSETS.fetch(c.req.raw));

export default app;
