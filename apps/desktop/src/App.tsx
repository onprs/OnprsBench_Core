import { useEffect, useState } from "react";
import { HashRouter, Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "@/components/Layout";
import { apiBase } from "@/lib/api";
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

export function App() {
  return (
    <HashRouter>
      <BackendGate>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/" element={<Navigate to="/setup" replace />} />
            <Route path="/setup" element={<SetupPage />} />
            <Route path="/datasets" element={<DatasetsPage />} />
            <Route path="/runs/new" element={<NewRunPage />} />
            <Route path="/runs/:runId" element={<RunDetailPage />} />
            <Route path="/history" element={<HistoryPage />} />
            <Route path="/compare" element={<ComparePage />} />
          </Route>
        </Routes>
      </BackendGate>
    </HashRouter>
  );
}
