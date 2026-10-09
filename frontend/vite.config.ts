import fs from 'node:fs'
import path from 'node:path'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Same mkcert certificate as the backend (created by `make certs`).
const certsDir = path.resolve(import.meta.dirname, '../certs')
const certFile = path.join(certsDir, 'dev.pem')
const keyFile = path.join(certsDir, 'dev-key.pem')
const haveCerts = fs.existsSync(certFile) && fs.existsSync(keyFile)
if (!haveCerts) {
  console.warn(
    '[vite] certs/dev.pem not found; run `make certs`. Serving plain http (phones will not get a camera).',
  )
}

// Backend to proxy /api and /ws to in development (publicsafe run on 8443).
const backend = process.env.VITE_BACKEND_URL ?? 'https://localhost:8443'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    strictPort: true,
    https: haveCerts
      ? { cert: fs.readFileSync(certFile), key: fs.readFileSync(keyFile) }
      : undefined,
    proxy: {
      // secure:false because Node does not use the macOS trust store for the mkcert CA.
      '/api': { target: backend, secure: false },
      '/help': { target: backend, secure: false },
      '/ws': { target: backend.replace(/^http/, 'ws'), ws: true, secure: false },
    },
  },
  build: { outDir: 'dist', emptyOutDir: true, sourcemap: false },
})
