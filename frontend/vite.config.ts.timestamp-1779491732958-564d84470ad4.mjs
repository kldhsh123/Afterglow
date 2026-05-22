// vite.config.ts
import { defineConfig } from "file:///mnt/j/vsrepos/Afterglow/frontend/node_modules/.pnpm/vite@5.4.21_@types+node@22.19.19/node_modules/vite/dist/node/index.js";
import vue from "file:///mnt/j/vsrepos/Afterglow/frontend/node_modules/.pnpm/@vitejs+plugin-vue@5.2.4_vite@5.4.21_@types+node@22.19.19__vue@3.5.34_typescript@5.9.3_/node_modules/@vitejs/plugin-vue/dist/index.mjs";
import { fileURLToPath, URL } from "node:url";
var __vite_injected_original_import_meta_url = "file:///mnt/j/vsrepos/Afterglow/frontend/vite.config.ts";
var vite_config_default = defineConfig(({ mode }) => {
  const backend = process.env.VITE_BACKEND_URL || "http://127.0.0.1:8000";
  return {
    plugins: [vue()],
    resolve: {
      alias: {
        "@": fileURLToPath(new URL("./src", __vite_injected_original_import_meta_url))
      }
    },
    server: {
      port: 5173,
      strictPort: false,
      proxy: {
        "/v1": { target: backend, changeOrigin: true },
        "/memory": { target: backend, changeOrigin: true },
        "/info": { target: backend, changeOrigin: true },
        "/healthz": { target: backend, changeOrigin: true },
        "/readyz": { target: backend, changeOrigin: true },
        "/debug": { target: backend, changeOrigin: true },
        "/setup": { target: backend, changeOrigin: true },
        "/images": { target: backend, changeOrigin: true }
      }
    },
    build: {
      outDir: "dist",
      sourcemap: mode !== "production"
    }
  };
});
export {
  vite_config_default as default
};
//# sourceMappingURL=data:application/json;base64,ewogICJ2ZXJzaW9uIjogMywKICAic291cmNlcyI6IFsidml0ZS5jb25maWcudHMiXSwKICAic291cmNlc0NvbnRlbnQiOiBbImNvbnN0IF9fdml0ZV9pbmplY3RlZF9vcmlnaW5hbF9kaXJuYW1lID0gXCIvbW50L2ovdnNyZXBvcy9BZnRlcmdsb3cvZnJvbnRlbmRcIjtjb25zdCBfX3ZpdGVfaW5qZWN0ZWRfb3JpZ2luYWxfZmlsZW5hbWUgPSBcIi9tbnQvai92c3JlcG9zL0FmdGVyZ2xvdy9mcm9udGVuZC92aXRlLmNvbmZpZy50c1wiO2NvbnN0IF9fdml0ZV9pbmplY3RlZF9vcmlnaW5hbF9pbXBvcnRfbWV0YV91cmwgPSBcImZpbGU6Ly8vbW50L2ovdnNyZXBvcy9BZnRlcmdsb3cvZnJvbnRlbmQvdml0ZS5jb25maWcudHNcIjtpbXBvcnQgeyBkZWZpbmVDb25maWcgfSBmcm9tICd2aXRlJ1xuaW1wb3J0IHZ1ZSBmcm9tICdAdml0ZWpzL3BsdWdpbi12dWUnXG5pbXBvcnQgeyBmaWxlVVJMVG9QYXRoLCBVUkwgfSBmcm9tICdub2RlOnVybCdcblxuLy8gVml0ZSBcdTkxNERcdTdGNkVcbi8vIC0gXHU1RjAwXHU1M0QxXHU2NUY2XHU2MjhBXHU1NDBFXHU3QUVGIEFQSSBcdThERUZcdTVGODRcdTRFRTNcdTc0MDZcdTUyMzBcdTY3MkNcdTU3MzAgRmFzdEFQSVx1RkYwOFx1OUVEOFx1OEJBNCA4MDAwXHVGRjA5XG4vLyAtIFx1NTE0MVx1OEJCOFx1OTAxQVx1OEZDNyBWSVRFX0JBQ0tFTkRfVVJMIFx1NzNBRlx1NTg4M1x1NTNEOFx1OTFDRlx1ODk4Nlx1NzZENlxuZXhwb3J0IGRlZmF1bHQgZGVmaW5lQ29uZmlnKCh7IG1vZGUgfSkgPT4ge1xuICBjb25zdCBiYWNrZW5kID0gcHJvY2Vzcy5lbnYuVklURV9CQUNLRU5EX1VSTCB8fCAnaHR0cDovLzEyNy4wLjAuMTo4MDAwJ1xuICByZXR1cm4ge1xuICAgIHBsdWdpbnM6IFt2dWUoKV0sXG4gICAgcmVzb2x2ZToge1xuICAgICAgYWxpYXM6IHtcbiAgICAgICAgJ0AnOiBmaWxlVVJMVG9QYXRoKG5ldyBVUkwoJy4vc3JjJywgaW1wb3J0Lm1ldGEudXJsKSksXG4gICAgICB9LFxuICAgIH0sXG4gICAgc2VydmVyOiB7XG4gICAgICBwb3J0OiA1MTczLFxuICAgICAgc3RyaWN0UG9ydDogZmFsc2UsXG4gICAgICBwcm94eToge1xuICAgICAgICAnL3YxJzogeyB0YXJnZXQ6IGJhY2tlbmQsIGNoYW5nZU9yaWdpbjogdHJ1ZSB9LFxuICAgICAgICAnL21lbW9yeSc6IHsgdGFyZ2V0OiBiYWNrZW5kLCBjaGFuZ2VPcmlnaW46IHRydWUgfSxcbiAgICAgICAgJy9pbmZvJzogeyB0YXJnZXQ6IGJhY2tlbmQsIGNoYW5nZU9yaWdpbjogdHJ1ZSB9LFxuICAgICAgICAnL2hlYWx0aHonOiB7IHRhcmdldDogYmFja2VuZCwgY2hhbmdlT3JpZ2luOiB0cnVlIH0sXG4gICAgICAgICcvcmVhZHl6JzogeyB0YXJnZXQ6IGJhY2tlbmQsIGNoYW5nZU9yaWdpbjogdHJ1ZSB9LFxuICAgICAgICAnL2RlYnVnJzogeyB0YXJnZXQ6IGJhY2tlbmQsIGNoYW5nZU9yaWdpbjogdHJ1ZSB9LFxuICAgICAgICAnL3NldHVwJzogeyB0YXJnZXQ6IGJhY2tlbmQsIGNoYW5nZU9yaWdpbjogdHJ1ZSB9LFxuICAgICAgICAnL2ltYWdlcyc6IHsgdGFyZ2V0OiBiYWNrZW5kLCBjaGFuZ2VPcmlnaW46IHRydWUgfSxcbiAgICAgIH0sXG4gICAgfSxcbiAgICBidWlsZDoge1xuICAgICAgb3V0RGlyOiAnZGlzdCcsXG4gICAgICBzb3VyY2VtYXA6IG1vZGUgIT09ICdwcm9kdWN0aW9uJyxcbiAgICB9LFxuICB9XG59KVxuIl0sCiAgIm1hcHBpbmdzIjogIjtBQUFxUixTQUFTLG9CQUFvQjtBQUNsVCxPQUFPLFNBQVM7QUFDaEIsU0FBUyxlQUFlLFdBQVc7QUFGdUksSUFBTSwyQ0FBMkM7QUFPM04sSUFBTyxzQkFBUSxhQUFhLENBQUMsRUFBRSxLQUFLLE1BQU07QUFDeEMsUUFBTSxVQUFVLFFBQVEsSUFBSSxvQkFBb0I7QUFDaEQsU0FBTztBQUFBLElBQ0wsU0FBUyxDQUFDLElBQUksQ0FBQztBQUFBLElBQ2YsU0FBUztBQUFBLE1BQ1AsT0FBTztBQUFBLFFBQ0wsS0FBSyxjQUFjLElBQUksSUFBSSxTQUFTLHdDQUFlLENBQUM7QUFBQSxNQUN0RDtBQUFBLElBQ0Y7QUFBQSxJQUNBLFFBQVE7QUFBQSxNQUNOLE1BQU07QUFBQSxNQUNOLFlBQVk7QUFBQSxNQUNaLE9BQU87QUFBQSxRQUNMLE9BQU8sRUFBRSxRQUFRLFNBQVMsY0FBYyxLQUFLO0FBQUEsUUFDN0MsV0FBVyxFQUFFLFFBQVEsU0FBUyxjQUFjLEtBQUs7QUFBQSxRQUNqRCxTQUFTLEVBQUUsUUFBUSxTQUFTLGNBQWMsS0FBSztBQUFBLFFBQy9DLFlBQVksRUFBRSxRQUFRLFNBQVMsY0FBYyxLQUFLO0FBQUEsUUFDbEQsV0FBVyxFQUFFLFFBQVEsU0FBUyxjQUFjLEtBQUs7QUFBQSxRQUNqRCxVQUFVLEVBQUUsUUFBUSxTQUFTLGNBQWMsS0FBSztBQUFBLFFBQ2hELFVBQVUsRUFBRSxRQUFRLFNBQVMsY0FBYyxLQUFLO0FBQUEsUUFDaEQsV0FBVyxFQUFFLFFBQVEsU0FBUyxjQUFjLEtBQUs7QUFBQSxNQUNuRDtBQUFBLElBQ0Y7QUFBQSxJQUNBLE9BQU87QUFBQSxNQUNMLFFBQVE7QUFBQSxNQUNSLFdBQVcsU0FBUztBQUFBLElBQ3RCO0FBQUEsRUFDRjtBQUNGLENBQUM7IiwKICAibmFtZXMiOiBbXQp9Cg==
