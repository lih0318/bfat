import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Backend URL for dev proxy. Default: same machine. On server, set VITE_PROXY_API_TARGET if backend runs elsewhere.
const apiTarget = process.env.VITE_PROXY_API_TARGET ?? 'http://0.0.0.0:8000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    allowedHosts: true, // allow EC2 host, IP, and any domain
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
})
