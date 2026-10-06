/**
 * 开发环境编排：同时启动 Python sidecar（FastAPI）与 Vite dev server。
 * 由 `tauri dev` 的 beforeDevCommand 调用，也可单独运行（纯浏览器开发）。
 *
 * 两者都按“已在运行则复用”的规则处理，避免重复拉起导致端口冲突：
 * - 8765 有健康 sidecar → 复用，避免多实例写同一数据库；
 * - 14200 已是本项目前端 → 复用；被其他程序占用 → 明确报错并退出；
 * - 任一子进程异常退出时打印原因并以非零码结束，不再静默失败。
 *
 * 说明：探测使用 node:http（agent: false）而非全局 fetch——undici 的
 * keep-alive 连接会在 Windows 上让 process.exit 触发 libuv 断言，导致
 * 本应成功的 dev 命令以异常码结束。
 */
import { spawn } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

const desktopDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const serverDir = path.resolve(desktopDir, "../../server");
// Vite 默认监听 localhost（Windows 上可能是 IPv6 ::1），逐个地址探测
const FRONTEND_URLS = ["http://localhost:14200/", "http://127.0.0.1:14200/"];
const BACKEND_META_URL = "http://127.0.0.1:8765/api/meta";

const venvPython =
  process.platform === "win32"
    ? path.join(serverDir, ".venv", "Scripts", "python.exe")
    : path.join(serverDir, ".venv", "bin", "python");

let pythonCmd = venvPython;
if (!fs.existsSync(venvPython)) {
  console.warn(`[dev] 未找到 ${venvPython}，回退到系统 python（需已安装 server/requirements.txt）`);
  pythonCmd = "python";
}

let server = null;
let vite = null;

/** 单次 HTTP GET 探测；不可达/超时返回 null。 */
function httpProbe(url) {
  return new Promise((resolve) => {
    const req = http.get(url, { agent: false, timeout: 2000 }, (resp) => {
      let body = "";
      resp.setEncoding("utf-8");
      resp.on("data", (chunk) => {
        body += chunk;
      });
      resp.on("end", () => resolve({ status: resp.statusCode ?? 0, body }));
      resp.on("error", () => resolve(null));
    });
    req.on("timeout", () => {
      req.destroy();
      resolve(null);
    });
    req.on("error", () => resolve(null));
  });
}

/** 探测 8765：已有健康 sidecar 时复用，否则启动新的。 */
async function spawnServer() {
  const probe = await httpProbe(BACKEND_META_URL);
  if (probe && probe.status === 200) {
    console.log("[dev] 检测到已有 sidecar 在 127.0.0.1:8765 运行，直接复用");
    return null;
  }
  const child = spawn(pythonCmd, ["-m", "app.main"], { cwd: serverDir, stdio: "inherit" });
  child.on("exit", (code) => {
    console.error(`[dev] python sidecar 退出（code=${code}）`);
    shutdown(code === 0 ? 0 : 1);
  });
  return child;
}

/** 探测 14200：free / ours（本项目前端）/ foreign（其他程序占用）。 */
async function probeFrontend() {
  for (const url of FRONTEND_URLS) {
    const probe = await httpProbe(url);
    if (!probe) continue;
    if (probe.status !== 200) return "foreign";
    return probe.body.includes("OnprsBench") ? "ours" : "foreign";
  }
  return "free";
}

function spawnVite() {
  const child = spawn("pnpm dev", { cwd: desktopDir, stdio: "inherit", shell: true });
  child.on("exit", (code) => {
    if (code !== 0) {
      console.error(`[dev] 前端 dev server 退出（code=${code}）`);
      shutdown(1);
      return;
    }
    shutdown(0);
  });
  return child;
}

function shutdown(code = 0) {
  process.exitCode = code;
  server?.kill();
  vite?.kill();
  // 兜底：子进程未及时回收时强制退出，避免 dev 命令悬挂
  setTimeout(() => process.exit(code), 500).unref();
}

process.on("SIGINT", () => shutdown(0));
process.on("SIGTERM", () => shutdown(0));

server = await spawnServer();

const frontend = await probeFrontend();
if (frontend === "ours") {
  console.log("[dev] 检测到已有前端服务在 127.0.0.1:14200 运行，直接复用");
} else if (frontend === "foreign") {
  console.error("[dev] 端口 14200 已被其他程序占用（不是本项目的前端服务）。");
  console.error("[dev] 请关闭占用该端口的进程后重试；排查命令：netstat -ano | findstr :14200");
  shutdown(1);
} else {
  vite = spawnVite();
}

// 两端都在运行时无需守护进程，脚本自然结束并返回 0；否则由子进程维持运行
if (!vite && !server && frontend === "ours") {
  console.log("[dev] 后端与前端均已就绪，无需启动新进程");
}
