import { defineConfig, loadEnv } from 'vite'
import type { ServerOptions } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
//
// WHAT CHANGED, AND WHY EACH ONE MATTERS
// --------------------------------------
// The plugin list is untouched: react() + tailwindcss() as before. Everything
// below is the dev/preview server, which had no configuration at all and
// therefore took every default.
//
// 1. `strictPort: true`. Vite's default port is 5173, but if that port is taken
//    it silently moves to 5174 and prints one quiet line. The backend allows a
//    fixed list of origins (CORS_ALLOWED_ORIGINS), so the app then loads and
//    every request fails with a CORS error that looks like a backend problem.
//    With strictPort the dev server refuses to start instead, and says why.
//
// 2. `host` stays localhost unless you ask for otherwise. A dev server has no
//    authentication: anything that can reach it can read every filing the
//    dashboard can fetch. Set VITE_DEV_HOST=0.0.0.0 in `.env` (deliberately,
//    on a machine you trust) if another desk needs to open it.
//
// 3. A proxy for `/api/v1` only — nothing else is forwarded. It is what makes
//    same-origin development possible: instead of the browser calling
//    http://localhost:8000 directly (which requires CORS to be right, and breaks
//    the moment the frontend is opened by IP or behind HTTPS), Vite forwards the
//    API calls itself and the browser only ever talks to the dev server.
//
//    It is INERT until you set it up: with no VITE_API_BASE_URL the client keeps
//    calling http://<hostname>:8000 and this proxy is simply never used. To turn
//    it on, put this in the frontend `.env`:
//
//        VITE_API_BASE_URL=/
//
//    The value must be `/` and not empty. `api.ts` does
//    `VITE_API_BASE_URL || http://<host>:8000`, so an empty string is falsy and
//    silently falls back to the absolute URL — the PDF viewer honours an empty
//    value (it checks for undefined), so `''` would leave the two halves of the
//    app pointed at different origins. `/` is the one value both agree on:
//    axios joins `/` + `/api/v1/...` into a relative request, and the document
//    resolver trims the trailing slash away.
//
// 4. `allowedHosts` is declared only when VITE_DEV_ALLOWED_HOSTS is set. Vite 6
//    (and 5.4.19+) refuses requests whose Host header it does not recognise —
//    "Blocked request. This host is not allowed." — which is what you get when
//    the dev server is opened as http://pcf-dev.bot.internal:5173 rather than
//    through localhost. List those names in the env var when you need them:
//
//        VITE_DEV_ALLOWED_HOSTS=pcf-dev.bot.internal,10.10.4.21
//
//    It is assigned through a cast below, so the file still type-checks against
//    a `vite` older than 6.
//
// 5. `preview` mirrors the dev settings, so `npm run build && npm run preview`
//    behaves the same way instead of moving to port 4174 unnoticed.
//
// NOTHING HERE CHANGES THE PRODUCTION BUILD. `vite build` output is unaffected
// by the server block; the only production-facing setting is the one you already
// had — `base`, which is still the default `/`.
export default defineConfig(({ mode }) => {
  // Prefix `VITE_` only: this reads the frontend's .env without pulling the
  // whole file (and any secret in it) into the config process.
  const env = loadEnv(mode, process.cwd(), 'VITE_')

  const devHost = (env.VITE_DEV_HOST || '').trim() || 'localhost'
  const devPort = Number((env.VITE_DEV_PORT || '').trim() || 5173)
  const previewPort = Number((env.VITE_PREVIEW_PORT || '').trim() || 4173)

  // Where the FastAPI backend is listening during development. This is a
  // server-to-server address, so it is localhost even when VITE_DEV_HOST is not:
  // the proxy runs on the machine running Vite.
  const apiTarget = (env.VITE_DEV_API_TARGET || '').trim() || 'http://localhost:8000'

  const allowedHosts = (env.VITE_DEV_ALLOWED_HOSTS || '')
    .split(',')
    .map((name) => name.trim())
    .filter(Boolean)

  const server: ServerOptions = {
    host: devHost,
    port: devPort,
    strictPort: true,
    proxy: {
      // Scoped on purpose. Only the API prefix is forwarded — `/`, `/files` and
      // everything else are still served by Vite from this project, so a typo in
      // a URL cannot quietly turn the dev server into a pass-through to the
      // backend.
      '/api/v1': {
        target: apiTarget,
        // The backend's CORS list is about browser origins; through a proxy the
        // browser never makes a cross-origin request. changeOrigin is left false
        // so the backend sees the Host it would see if you called it directly.
        changeOrigin: false,
        // true when the proxy target is HTTPS.
        secure: false,
      },
    },
  }

  if (allowedHosts.length) {
    // Vite 6+ / 5.4.19+. Assigned through a cast so this file still compiles
    // against an older `vite` type definition.
    ;(server as Record<string, unknown>).allowedHosts = allowedHosts
  }

  return {
    plugins: [
      react(),
      tailwindcss(),
    ],
    server,
    preview: {
      host: devHost,
      port: previewPort,
      strictPort: true,
    },
  }
})
