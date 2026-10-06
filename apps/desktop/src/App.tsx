import { useEffect, useState } from "react";
import { HashRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { Layout } from "@/components/Layout";
import { apiBase } from "@/lib/api";
import { cn } from "@/lib/utils";
import { ComparePage } from "@/pages/ComparePage";
import { DatasetsPage } from "@/pages/DatasetsPage";
import { HistoryPage } from "@/pages/HistoryPage";
import { NewRunPage } from "@/pages/NewRunPage";
import { RunDetailPage } from "@/pages/RunDetailPage";
import { SetupPage } from "@/pages/SetupPage";

/** 等待本地 sidecar 就绪后再渲染业务页面（冷启动时后端需要数秒初始化）。 */
function BackendGate({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      while (!cancelled) {
        try {
          const resp = await fetch(`${apiBase()}/api/meta`);
          if (resp.ok) {
            setReady(true);
            return;
          }
        } catch {
          // sidecar 尚未就绪，继续等待
        }
        await new Promise((r) => setTimeout(r, 800));
      }
    };
    void poll();
    return () => {
      cancelled = true;
    };
  }, []);

  if (!ready) {
    return (
      <div className="flex h-screen items-center justify-center">
        <p className="text-sm text-muted-foreground">正在启动本地服务…</p>
      </div>
    );
  }
  return <>{children}</>;
}

/** 根据路径渲染对应页面（已访问页面由 KeepAlivePages 缓存挂载）。 */
function renderPage(pathname: string) {
  if (pathname.startsWith("/runs/") && pathname !== "/runs/new") {
    return <RunDetailPage runId={pathname.split("/")[2]} />;
  }
  switch (pathname) {
    case "/setup":
      return <SetupPage />;
    case "/datasets":
      return <DatasetsPage />;
    case "/runs/new":
      return <NewRunPage />;
    case "/history":
      return <HistoryPage />;
    case "/compare":
      return <ComparePage />;
    default:
      return <Navigate to="/setup" replace />;
  }
}

/** 页面缓存：切换导航时保留表单、选择与滚动位置（已访问页面隐藏而非卸载）。 */
function KeepAlivePages() {
  const { pathname } = useLocation();
  const [visited, setVisited] = useState<string[]>(() => [pathname]);

  useEffect(() => {
    setVisited((prev) => (prev.includes(pathname) ? prev : [...prev, pathname]));
  }, [pathname]);

  return (
    <>
      {visited.map((path) => (
        <div
          key={path}
          className={cn("min-h-0 flex-1 overflow-auto p-6", path !== pathname && "hidden")}
        >
          {renderPage(path)}
        </div>
      ))}
    </>
  );
}

export function App() {
  return (
    <HashRouter>
      <BackendGate>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/" element={<Navigate to="/setup" replace />} />
            <Route path="*" element={<KeepAlivePages />} />
          </Route>
        </Routes>
      </BackendGate>
    </HashRouter>
  );
}
