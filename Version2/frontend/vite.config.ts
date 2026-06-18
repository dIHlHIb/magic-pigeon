import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The Python backend (Flask + Socket.IO) listens here. In dev the Vite server
// proxies API + WebSocket traffic to it, so the browser only ever talks to its
// own origin — this sidesteps the backend's strict CORS allowlist (and the fact
// that the REST API sends no CORS headers) without touching the backend.
const BACKEND = 'http://127.0.0.1:5001'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: BACKEND, changeOrigin: true },
      '/socket.io': {
        target: BACKEND,
        ws: true,
        changeOrigin: true,
        // Socket.IO's handshake CORS check rejects the dev-server origin
        // (:5173). Rewrite the Origin header on the proxied request/upgrade so
        // it matches the backend's allowlist.
        configure: (proxy) => {
          proxy.on('proxyReq', (proxyReq) => proxyReq.setHeader('origin', BACKEND))
          proxy.on('proxyReqWs', (proxyReq) => proxyReq.setHeader('origin', BACKEND))
        },
      },
    },
  },
})
