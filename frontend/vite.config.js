import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": "http://127.0.0.1:8010",
      "/health": "http://127.0.0.1:8010",
    },
  },
  build: {
    rolldownOptions: {
      output: {
        manualChunks: (id) =>
          id.includes("/echarts/") || id.includes("/zrender/")
            ? "charts"
            : id.includes("/element-plus/")
              ? "element"
              : undefined,
      },
    },
  },
});
