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
      "/api": { target: "http://127.0.0.1:8765", changeOrigin: true },
    },
  },
  build: { chunkSizeWarningLimit: 1500 },
});
