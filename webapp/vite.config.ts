import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'

const productionSourcemap = String(process.env.VITE_PRODUCTION_SOURCEMAP || '').toLowerCase() === 'true'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  build: {
    outDir: '../frontend_dist',
    emptyOutDir: true,
    sourcemap: productionSourcemap,
    // Production stability fix:
    // Do not force React / React-DOM into a custom manual chunk.
    // Previous vendor-react manual chunk caused React 19 runtime bootstrap crash:
    // "Cannot set properties of undefined (setting 'Activity')".
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
  },
})
