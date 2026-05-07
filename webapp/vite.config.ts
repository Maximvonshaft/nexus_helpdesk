import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'

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
    sourcemap: process.env.VITE_PRODUCTION_SOURCEMAP === 'hidden' ? 'hidden' : false,
    chunkSizeWarningLimit: 180,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined
          if (id.includes('/react/') || id.includes('/react-dom/')) return 'react-vendor'
          if (id.includes('@tanstack/react-router') || id.includes('@tanstack/react-query')) return 'router-query'
          if (id.includes('@radix-ui')) return 'ui-vendor'
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
