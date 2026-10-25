"use strict";

// Network only: no private pages, API responses, exports or writes are cached or queued.
self.addEventListener("activate", event => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", event => {
  const request = event.request;
  const url = new URL(request.url);
  const scope = new URL(self.registration.scope);
  if (request.method !== "GET" || request.mode !== "navigate" ||
      url.origin !== scope.origin || url.pathname !== scope.pathname) return;
  event.respondWith(fetch(request).catch(() => new Response(`<!doctype html>
<html lang="nn"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#162d3e"><title>MeshPi · Fråkopla</title>
<style>body{margin:0;padding:10vh 24px;background:#162d3e;color:#e3edf4;font:18px/1.6 system-ui,sans-serif}main{max-width:36em;margin:auto}a{color:#a2ddeb}h1{line-height:1.2}</style>
</head><body><main><h1>MeshPi er fråkopla</h1>
<p>Appen treng samband til MeshPi-tenaren. Kontroller lokalnettet eller Tailscale, og prøv igjen.</p>
<p>Meldingar og nodeinformasjon er berre tilgjengelege når tenaren kan nåast.</p>
<p><a href="${scope.pathname}">Prøv igjen</a></p>
</main></body></html>`, {
    status: 503,
    headers: {
      "Content-Type": "text/html; charset=utf-8",
      "Cache-Control": "no-store",
      "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
      "Referrer-Policy": "no-referrer",
      "X-Content-Type-Options": "nosniff",
    },
  })));
});
