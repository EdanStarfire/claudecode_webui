import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'path'

// Get backend configuration from environment variables
const BACKEND_HOST = process.env.VITE_BACKEND_HOST || '172.17.0.1'
const BACKEND_PORT = process.env.VITE_BACKEND_PORT || '8001'
const BACKEND_URL = `http://${BACKEND_HOST}:${BACKEND_PORT}`

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [vue()],

  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src')
    }
  },

  server: {
    port: 5173,
    host: '0.0.0.0', // Listen on all network interfaces for development

    proxy: {
      // Proxy API requests to FastAPI backend
      '/api': {
        target: BACKEND_URL,
        changeOrigin: true,
        secure: false
      },

      // Proxy WebSocket connections to FastAPI backend
      '/ws': {
        target: BACKEND_URL.replace('http://', 'ws://'),
        ws: true,
        changeOrigin: true
      },

      // Proxy OAuth callback so redirect_uri can use window.location.origin in dev
      '/oauth': {
        target: BACKEND_URL,
        changeOrigin: true,
        secure: false
      }
    }
  },

  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    sourcemap: false,

    // Optimize build
    rollupOptions: {
      output: {
        manualChunks: {
          'vue-vendor': ['vue', 'vue-router', 'pinia'],
          'markdown-vendor': ['marked', 'dompurify'],
          'mermaid-vendor': ['mermaid'],
          'diff-vendor': ['diff', 'diff2html']
        }
      }
    }
  }
})
