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
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined
          if (id.includes('/react') || id.includes('/react-dom')) return 'vendor-react'
          if (id.includes('@tanstack')) return 'vendor-tanstack'
          if (id.includes('@radix-ui')) return 'vendor-radix'
          return 'vendor'
        },
      },
    },
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
  },
})
