import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Hosts the dev server will answer to, beyond localhost. Vite refuses unknown Host headers by
// default — without that, a page on another site could point a hostname it controls at
// 127.0.0.1:5173 and read what came back, which is a DNS-rebinding attack on your dev server.
//
// A leading dot matches subdomains, so one entry covers every tunnel from that provider. That
// is a real loosening: anyone who can get a hostname under that domain — which, for a public
// tunnel provider, is anyone — can reach this server while it's running. Acceptable for a dev
// server you start for a demo and stop afterwards, and not something to copy into anything
// long-lived.
//
// Add to it per machine with DEV_ALLOWED_HOSTS, comma-separated. No VITE_ prefix on purpose:
// that prefix ships a variable to the browser, and this is server configuration.
//
// Both spellings of the domain are listed because a leading dot is documented as matching
// subdomains, and relying on it to also match the bare domain would make `futurequest.app`
// itself depend on a detail of Vite's matching. Two entries cost nothing.
const SITE_HOSTS = ["futurequest.app", ".futurequest.app"];

const TUNNEL_HOSTS = [".trycloudflare.com", ".ngrok-free.app", ".ngrok.app"];

const allowedHosts = [
  ...SITE_HOSTS,
  ...TUNNEL_HOSTS,
  ...(process.env.DEV_ALLOWED_HOSTS ?? "")
    .split(",")
    .map((host) => host.trim())
    .filter(Boolean),
];

// Shared between `dev` and `preview`: the browser talks to Vite, which forwards to FastAPI.
// Same-origin from the browser's point of view, so CORS never comes into it. The CORS
// middleware on the backend is for deployments where the two are served separately.
const proxy = {
  "/api": {
    target: "http://127.0.0.1:8000",
    // `changeOrigin` deliberately left off. It would rewrite Host to 127.0.0.1:8000, and the
    // backend needs the browser-facing host to pick its origin out of ALLOWED_ORIGINS —
    // that's how the Google redirect lands back on the right domain when this is served from
    // somewhere other than localhost.
    xfwd: true,
  },
};

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    allowedHosts,
    proxy,
  },
  // `preview` serves the built bundle and keeps its own host list and proxy — neither is
  // inherited from `server`. Worth configuring because it's the quickest honest way to serve
  // the real build on the domain; a static file server in front of `dist/` is the end state,
  // and then neither of these settings applies at all.
  preview: {
    port: 4173,
    allowedHosts,
    proxy,
  },
});
