// Google sign-in via OpenID Connect (authorization-code flow with PKCE + state).

import type { Context } from "hono";

import { randomToken, sha256B64url, stringFromB64url } from "./crypto";
import { linkIdentity, upsertUser } from "./db";
import { startSession } from "./session";
import type { Env } from "./types";

const AUTHORIZE = "https://accounts.google.com/o/oauth2/v2/auth";
const TOKEN = "https://oauth2.googleapis.com/token";

interface GoogleIdClaims {
  sub: string;
  email?: string;
  email_verified?: boolean;
  name?: string;
}

export async function googleStart(c: Context<Env>): Promise<Response> {
  const state = randomToken(24);
  const verifier = randomToken(32);
  const challenge = await sha256B64url(verifier);
  // Bind the PKCE verifier to this login attempt; short TTL.
  await c.env.KV.put(`oauth:${state}`, verifier, { expirationTtl: 600 });

  const url = new URL(AUTHORIZE);
  url.search = new URLSearchParams({
    client_id: c.env.GOOGLE_CLIENT_ID,
    redirect_uri: `${c.env.APP_URL}/auth/google/callback`,
    response_type: "code",
    scope: "openid email profile",
    state,
    code_challenge: challenge,
    code_challenge_method: "S256",
    access_type: "online",
    prompt: "select_account",
  }).toString();
  return c.redirect(url.toString());
}

export async function googleCallback(c: Context<Env>): Promise<Response> {
  const code = c.req.query("code");
  const state = c.req.query("state");
  if (!code || !state) return c.text("missing code/state", 400);

  const verifier = await c.env.KV.get(`oauth:${state}`);
  if (!verifier) return c.text("invalid or expired state", 400);
  await c.env.KV.delete(`oauth:${state}`);

  const resp = await fetch(TOKEN, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      code,
      client_id: c.env.GOOGLE_CLIENT_ID,
      client_secret: c.env.GOOGLE_CLIENT_SECRET,
      redirect_uri: `${c.env.APP_URL}/auth/google/callback`,
      grant_type: "authorization_code",
      code_verifier: verifier,
    }),
  });
  if (!resp.ok) return c.text(`token exchange failed: ${await resp.text()}`, 502);

  const token = (await resp.json()) as { id_token?: string };
  if (!token.id_token) return c.text("no id_token in response", 502);

  // The id_token came directly from Google's token endpoint over TLS using our
  // client secret, so its claims are trustworthy without re-verifying the JWKS
  // signature here. (Add JWKS verification for defense-in-depth if desired.)
  const claims = decodeJwtClaims<GoogleIdClaims>(token.id_token);
  if (!claims?.email) return c.text("no email in id_token", 502);

  const user = await upsertUser(c.env.DB, claims.email, claims.name ?? null);
  await linkIdentity(c.env.DB, user.id, "google", claims.sub);
  await startSession(c, user.id);
  return c.redirect(c.env.APP_URL);
}

function decodeJwtClaims<T>(jwt: string): T | null {
  const parts = jwt.split(".");
  if (parts.length < 2) return null;
  try {
    return JSON.parse(stringFromB64url(parts[1])) as T;
  } catch {
    return null;
  }
}
