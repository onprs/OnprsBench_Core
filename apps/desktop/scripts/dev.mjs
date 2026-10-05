/**
 * 开发环境编排：同时启动 Python sidecar（FastAPI）与 Vite dev server。
 * 由 `tauri dev` 的 beforeDevCommand 调用，也可单独运行（纯浏览器开发）。
 */
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const desktopDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const serverDir = path.resolve(desktopDir, "../../server");

const venvPython =
  process.platform === "win32"
    ? path.join(serverDir, ".venv", "Scripts", "python.exe")
    : path.join(serverDir, ".venv", "bin", "python");

let pythonCmd = venvPython;
if (!fs.existsSync(venvPython)) {
  console.warn(`[dev] 未找到 ${venvPython}，回退到系统 python（需已安装 server/requirements.txt）`);
  pythonCmd = "python";
}

const server = spawn(pythonCmd, ["-m", "app.main"], { cwd: serverDir, stdio: "inherit" });
const vite = spawn("pnpm dev", {
  cwd: desktopDir,
  stdio: "inherit",
  shell: true,
});

function shutdown() {
  server.kill();
  vite.kill();
  process.exit(0);
}

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
vite.on("exit", shutdown);
server.on("exit", (code) => {
  console.error(`[dev] python sidecar 退出（code=${code}）`);
  shutdown();
});
