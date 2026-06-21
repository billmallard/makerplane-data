// Stateless signed-cookie sessions: the cookie is `<payload>.<hmac>` where
// payload is base64url(JSON {uid, exp}). No server-side session store needed.

import type { Context, Next } from "hono";
import { getCookie, setCookie, deleteCookie } from "hono/cookie";

import { b64urlFromString, hmacSign, hmacVerify, stringFromB64url } from "./crypto";
import type { Env } from "./types";

const COOKIE = "sid";
const TTL_SECONDS = 60 * 60 * 24 * 30; // 30 days

export async function startSession(c: Context<Env>, userId: number): Promise<void> {
  const exp = Math.floor(Date.now() / 1000) + TTL_SECONDS;
  const payload = b64urlFromString(JSON.stringify({ uid: userId, exp }));
  const sig = await hmacSign(c.env.SESSION_SECRET, payload);
  setCookie(c, COOKIE, `${payload}.${sig}`, {
    httpOnly: true,
    secure: true,
    sameSite: "Lax",
    path: "/",
    maxAge: TTL_SECONDS,
  });
}

export function endSession(c: Context<Env>): void {
  deleteCookie(c, COOKIE, { path: "/" });
}

export async function currentUserId(c: Context<Env>): Promise<number | null> {
  const raw = getCookie(c, COOKIE);
  if (!raw) return null;
  const dot = raw.lastIndexOf(".");
  if (dot < 0) return null;
  const payload = raw.slice(0, dot);
  const sig = raw.slice(dot + 1);
  if (!(await hmacVerify(c.env.SESSION_SECRET, payload, sig))) return null;
  try {
    const data = JSON.parse(stringFromB64url(payload)) as { uid: number; exp: number };
    if (!data.exp || data.exp < Math.floor(Date.now() / 1000)) return null;
    return data.uid;
  } catch {
    return null;
  }
}

// Hono middleware: 401s unauthenticated requests, otherwise stashes userId.
export async function requireUser(c: Context<Env>, next: Next): Promise<Response | void> {
  const uid = await currentUserId(c);
  if (uid === null) return c.json({ error: "unauthorized" }, 401);
  c.set("userId", uid);
  await next();
}
