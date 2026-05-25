import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// During `npm run dev` the app runs on :5173 and proxies /api to the FastAPI
// backend (started via `codegraph serve --dev`). The packaged build (T6.6) is
// served same-origin, so the proxy is dev-only.
// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    // codegraph serve mounts this directory as the static site (T6.6).
    outDir: '../codegraph/server/static',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8765',
    },
  },
})
