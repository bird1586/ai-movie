import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

const API = process.env.AI_MOVIE_API ?? 'http://127.0.0.1:8110'

export default defineConfig({
  plugins: [vue()],
  server: {
    host: process.env.VITE_HOST ?? '127.0.0.1',
    port: 5180,
    strictPort: true,
    proxy: {
      '/api': API,
      '/ws': { target: API.replace(/^http/, 'ws'), ws: true },
    },
  },
})
