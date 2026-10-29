// SPDX-License-Identifier: Apache-2.0
// Cookieless Cloudflare Web Analytics for the site pages only (ruling D6).
// Every page on navdata.aerocommons.org loads this file; nothing a device
// downloads does (packs, manifest.json and report.json are not pages), so
// the updater stays telemetry-free.
//
// TOKEN is the aerocommons.org zone's existing Web Analytics site, the one
// the edge already injects on aerocommons.org and pyefis.aerocommons.org;
// navdata pageviews are told apart by hostname in that dashboard. The edge
// does not inject on navdata (served from R2), so this loads it by hand. The
// token is public: it is in the source of every aerocommons.org page.
// Empty means analytics off.
(function () {
  var TOKEN = "3522e4f857de4e05b4384a6a5777c4ed";
  if (!TOKEN) return;
  // If the edge ever starts injecting here too, one beacon is enough.
  if (document.querySelector("script[data-cf-beacon]")) return;
  var s = document.createElement("script");
  s.defer = true;
  s.src = "https://static.cloudflareinsights.com/beacon.min.js";
  s.setAttribute("data-cf-beacon", JSON.stringify({ token: TOKEN }));
  document.head.appendChild(s);
})();
