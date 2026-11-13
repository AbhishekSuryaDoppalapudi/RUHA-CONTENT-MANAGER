import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The FastAPI backend serves the built UI itself (python -m ruha.main). During UI development
// `npm run dev` proxies /api to it.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { '/api': 'http://127.0.0.1:8765' } },
  build: { outDir: 'dist', sourcemap: false, chunkSizeWarningLimit: 900 },
})
