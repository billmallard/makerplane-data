// Sends the magic-link email. Stub-friendly: if MAIL_API_KEY is unset the link
// is logged to the worker console (fine for local dev). Wire a transactional
// provider for production -- Resend's API is shown.

import type { Bindings } from "./types";

export async function sendMagicLink(env: Bindings, to: string, link: string): Promise<void> {
  if (!env.MAIL_API_KEY) {
    console.log(`[dev] magic link for ${to}: ${link}`);
    return;
  }
  const resp = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: {
      authorization: `Bearer ${env.MAIL_API_KEY}`,
      "content-type": "application/json",
    },
    body: JSON.stringify({
      from: env.MAIL_FROM ?? "login@aerocommons.org",
      to,
      subject: "Your AeroCommons sign-in link",
      text: `Sign in to the AeroCommons configurator:\n\n${link}\n\nThis link expires in 15 minutes. If you didn't request it, ignore this email.`,
    }),
  });
  if (!resp.ok) {
    throw new Error(`mail send failed: ${resp.status} ${await resp.text()}`);
  }
}
