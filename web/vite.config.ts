/// <reference types="vitest" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import { VitePWA } from 'vite-plugin-pwa'

const BRAND = '#0f0f10'

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      strategies: 'injectManifest',
      srcDir: 'src',
      filename: 'sw.ts',
      registerType: 'autoUpdate',
      injectRegister: false,
      includeAssets: ['icons/apple-touch-icon.png'],
      manifest: {
        name: 'Easo — HoReCa գնումներ',
        short_name: 'Easo',
        description: 'Закупки для кафе и ресторанов: все поставщики в одной корзине, можно торговаться.',
        lang: 'hy',
        start_url: '/',
        scope: '/',
        display: 'standalone',
        background_color: '#d3d6cf',
        theme_color: BRAND,
        icons: [
          { src: '/icons/icon-192.png', sizes: '192x192', type: 'image/png' },
          { src: '/icons/icon-512.png', sizes: '512x512', type: 'image/png' },
          { src: '/icons/icon-maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
      injectManifest: { globPatterns: ['**/*.{js,css,html,svg,png,woff2}'] },
      devOptions: { enabled: true, type: 'module' },
    }),
  ],
  server: {
    port: 5180,
    // The API has no /api prefix; nginx does the same rewrite in production
    proxy: { '/api': { target: 'http://localhost:8010', rewrite: (p) => p.replace(/^\/api/, '') } },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'], // e2e/ belongs to Playwright
  },
})
