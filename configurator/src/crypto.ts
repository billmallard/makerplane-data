// Small crypto helpers built on the Workers Web Crypto API (no dependencies):
// base64url, random tokens, SHA-256, and HMAC sign/verify for session cookies.

const encoder = new TextEncoder();

export function b64urlFromBytes(bytes: Uint8Array): string {
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function b64urlFromString(s: string): string {
  return b64urlFromBytes(encoder.encode(s));
}

export function bytesFromB64url(s: string): Uint8Array {
  const b64 = s.replace(/-/g, "+").replace(/_/g, "/");
  const pad = b64.length % 4 ? "=".repeat(4 - (b64.length % 4)) : "";
  const bin = atob(b64 + pad);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export function stringFromB64url(s: string): string {
  return new TextDecoder().decode(bytesFromB64url(s));
}

export function randomToken(nbytes = 32): string {
  const buf = new Uint8Array(nbytes);
  crypto.getRandomValues(buf);
  return b64urlFromBytes(buf);
}

export async function sha256B64url(input: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", encoder.encode(input));
  return b64urlFromBytes(new Uint8Array(digest));
}

async function hmacKey(secret: string): Promise<CryptoKey> {
  return crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign", "verify"],
  );
}

export async function hmacSign(secret: string, data: string): Promise<string> {
  const sig = await crypto.subtle.sign("HMAC", await hmacKey(secret), encoder.encode(data));
  return b64urlFromBytes(new Uint8Array(sig));
}

export async function hmacVerify(secret: string, data: string, sig: string): Promise<boolean> {
  let provided: Uint8Array;
  try {
    provided = bytesFromB64url(sig);
  } catch {
    return false;
  }
  return crypto.subtle.verify("HMAC", await hmacKey(secret), provided, encoder.encode(data));
}
