import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

// 配置向导构建脚本
// - base 设为 /config/：构建产物里 asset 路径会自动加这个前缀，匹配后端 mount 点
// - outDir 直接产到后端的 xuwen/web_ui/static/，无需手动拷贝
// - dev server 不做代理：dev 时由后端 /config/ 直接 serve，无 dev 体验
//   想要 dev hot reload 可在后端把 CONFIG_UI_ENABLED=true 并起 8000 端口，
//   然后 pnpm dev 起在 5174，手动改 fetch base 为 http://127.0.0.1:8000
export default defineConfig({
  base: '/config/',
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5174,
    strictPort: false,
    proxy: {
      '/config': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: '../xuwen/web_ui/static',
    emptyOutDir: true,
    sourcemap: false,
  },
})
