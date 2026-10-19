import { defineConfig, mergeConfig } from 'vite'
import base from '../vite.config'

export default mergeConfig(base, defineConfig({
  server: {
    host: '127.0.0.1', port: 5189, strictPort: true,
    proxy: { '/api': { target: 'http://127.0.0.1:8019', changeOrigin: true } },
  },
}))
