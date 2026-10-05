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

const server = await spawnServer();
const vite = spawn("pnpm dev", {
  cwd: desktopDir,
  stdio: "inherit",
  shell: true,
});

/** 若 8765 已有健康 sidecar（如上次运行残留），直接复用，避免多实例写同一数据库。 */
async function spawnServer() {
  try {
    const resp = await fetch("http://127.0.0.1:8765/api/meta", { signal: AbortSignal.timeout(2000) });
    if (resp.ok) {
      console.log("[dev] 检测到已有 sidecar 在 127.0.0.1:8765 运行，直接复用");
      return null;
    }
  } catch {
    // 没有可复用的 sidecar，正常启动
  }
  const child = spawn(pythonCmd, ["-m", "app.main"], { cwd: serverDir, stdio: "inherit" });
  child.on("exit", (code) => {
    console.error(`[dev] python sidecar 退出（code=${code}）`);
    shutdown();
  });
  return child;
}

function shutdown() {
  server?.kill();
  vite.kill();
  process.exit(0);
}

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
vite.on("exit", shutdown);
