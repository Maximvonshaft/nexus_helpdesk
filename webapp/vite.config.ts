import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'

const productionSourcemap = String(process.env.VITE_PRODUCTION_SOURCEMAP || '').toLowerCase() === 'true'

function npmPackageId(id: string): string | undefined {
  const marker = '/node_modules/'
  const index = id.lastIndexOf(marker)
  if (index === -1) return undefined

  const parts = id.slice(index + marker.length).split('/')
  if (!parts[0]) return undefined
  if (parts[0].startsWith('@') && parts[1]) return `${parts[0]}/${parts[1]}`
  return parts[0]
}

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
    manifest: true,
    sourcemap: productionSourcemap,
    rollupOptions: {
      output: {
        manualChunks(id) {
          const pkg = npmPackageId(id)
          if (!pkg) return undefined

          if (
            pkg === 'react' ||
            pkg === 'react-dom' ||
            pkg === 'scheduler' ||
            pkg === 'loose-envify' ||
            pkg === 'use-sync-external-store'
          ) {
            return 'vendor-react'
          }

          if (pkg === 'livekit-client' || pkg.startsWith('@livekit') || pkg === '@bufbuild/protobuf' || pkg === 'sdp') {
            return 'vendor-livekit'
          }

          if (pkg.startsWith('@tanstack')) return 'vendor-tanstack'
          if (pkg.startsWith('@radix-ui')) return 'vendor-radix'

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
