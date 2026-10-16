// The page's build and dev server (R223). In development the API runs separately (`uv run kg serve`, port 8000)
// and /api is proxied to it; `npm run build` writes dist/, which `kg serve` then serves itself.
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
  test: { environment: 'jsdom' },
})
