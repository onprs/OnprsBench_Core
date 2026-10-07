import react from "@vitejs/plugin-react";
import path from "path";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
  server: {
    port: 14200,
    strictPort: true,
    watch: {
      // tauri dev 会在 src-tauri/target 中编译，避免文件监视冲突
      ignored: ["**/src-tauri/**"],
    },
    proxy: {
      "/api": {
        // 可用 VITE_API_TARGET 指向其它后端（验证/多实例场景与正式实例隔离）
        target: process.env.VITE_API_TARGET ?? "http://127.0.0.1:8765",
        changeOrigin: true,
      },
    },
  },
  build: { chunkSizeWarningLimit: 1500 },
});
