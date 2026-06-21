// Passwordless email sign-in: request a magic link, then verify it. Only a hash
// of the one-time token is stored (in KV with a 15-minute TTL).

import type { Context } from "hono";

import { randomToken, sha256B64url } from "./crypto";
import { linkIdentity, upsertUser } from "./db";
import { sendMagicLink } from "./mail";
import { startSession } from "./session";
import type { Env } from "./types";

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

export async function emailRequest(c: Context<Env>): Promise<Response> {
  const body = await c.req.json<{ email?: string }>().catch(() => ({ email: undefined }));
  const email = body.email?.trim().toLowerCase();
  if (!email || !EMAIL_RE.test(email)) {
    return c.json({ error: "valid email required" }, 400);
  }

  const token = randomToken(32);
  const tokenHash = await sha256B64url(token);
  await c.env.KV.put(`magic:${tokenHash}`, email, { expirationTtl: 900 });

  const link = `${c.env.APP_URL}/auth/email/verify?token=${token}`;
  await sendMagicLink(c.env, email, link);

  // Always 200, regardless of whether the address is known, so the endpoint
  // can't be used to enumerate accounts.
  return c.json({ ok: true });
}

export async function emailVerify(c: Context<Env>): Promise<Response> {
  const token = c.req.query("token");
  if (!token) return c.text("missing token", 400);

  const tokenHash = await sha256B64url(token);
  const email = await c.env.KV.get(`magic:${tokenHash}`);
  if (!email) return c.text("invalid or expired link", 400);
  await c.env.KV.delete(`magic:${tokenHash}`); // single use

  const user = await upsertUser(c.env.DB, email, null);
  await linkIdentity(c.env.DB, user.id, "email", email);
  await startSession(c, user.id);
  return c.redirect(c.env.APP_URL);
}
