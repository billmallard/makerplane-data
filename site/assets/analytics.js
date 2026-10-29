// SPDX-License-Identifier: Apache-2.0
// Cookieless Cloudflare Web Analytics for the site pages only (ruling D6).
// Every page on navdata.aerocommons.org loads this file; nothing a device
// downloads does (packs, manifest.json and report.json are not pages), so
// the updater stays telemetry-free.
//
// TOKEN is the site token from Cloudflare dashboard > Analytics & Logs >
// Web Analytics > navdata.aerocommons.org (manual setup). It is public: it
// appears in every page that loads the beacon. Empty means analytics off.
(function () {
  var TOKEN = "";
  if (!TOKEN) return;
  var s = document.createElement("script");
  s.defer = true;
  s.src = "https://static.cloudflareinsights.com/beacon.min.js";
  s.setAttribute("data-cf-beacon", JSON.stringify({ token: TOKEN }));
  document.head.appendChild(s);
})();
