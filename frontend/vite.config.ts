import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 3000,
    host: '0.0.0.0',
    proxy: {
      '/api': {
        target: (() => {
          const envTarget = process.env.VITE_PROXY_TARGET
          // If running Vite locally on Windows, Docker DNS names like "backend" won't resolve.
          if (process.platform === 'win32' && envTarget && /\/\/backend(?::|\/|$)/.test(envTarget)) {
            return 'http://localhost:8000'
          }
          return envTarget || 'http://localhost:8000'
        })(),
        changeOrigin: true,
      },
    },
  },
})
