import { useEffect, useState } from "react";
import type { Window as TauriWindow } from "@tauri-apps/api/window";
import { Copy, Minus, Square, X } from "lucide-react";
import { isDesktopApp } from "@/lib/desktop";

/**
 * 自绘窗口标题栏：无边框窗口下提供拖动、最小化、最大化/还原与关闭。
 * 浏览器开发模式不渲染（由浏览器自身提供窗口框架）。
 */
export function AppTitleBar() {
  const [maximized, setMaximized] = useState(false);

  useEffect(() => {
    if (!isDesktopApp()) return;
    let unlisten: (() => void) | undefined;
    let disposed = false;
    void (async () => {
      const { getCurrentWindow } = await import("@tauri-apps/api/window");
      const appWindow = getCurrentWindow();
      setMaximized(await appWindow.isMaximized());
      const stop = await appWindow.onResized(() => {
        void appWindow.isMaximized().then(setMaximized);
      });
      if (disposed) stop();
      else unlisten = stop;
    })();
    return () => {
      disposed = true;
      unlisten?.();
    };
  }, []);

  async function withWindow(action: (appWindow: TauriWindow) => Promise<void>) {
    const { getCurrentWindow } = await import("@tauri-apps/api/window");
    await action(getCurrentWindow());
  }

  if (!isDesktopApp()) return null;

  return (
    <div
      data-tauri-drag-region
      className="flex h-9 shrink-0 select-none items-center justify-between border-b bg-card pl-3"
      onDoubleClick={() => void withWindow((appWindow) => appWindow.toggleMaximize())}
    >
      <span data-tauri-drag-region className="text-xs font-medium text-muted-foreground">
        OnprsBench
      </span>
      <div className="flex h-full" onDoubleClick={(e) => e.stopPropagation()}>
        <button
          type="button"
          className="flex h-full w-11 items-center justify-center text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          title="最小化"
          onClick={() => void withWindow((appWindow) => appWindow.minimize())}
        >
          <Minus className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          className="flex h-full w-11 items-center justify-center text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          title={maximized ? "还原" : "最大化"}
          onClick={() => void withWindow((appWindow) => appWindow.toggleMaximize())}
        >
          {maximized ? <Copy className="h-3 w-3" /> : <Square className="h-3 w-3" />}
        </button>
        <button
          type="button"
          className="flex h-full w-11 items-center justify-center text-muted-foreground transition-colors hover:bg-destructive hover:text-destructive-foreground"
          title="关闭"
          onClick={() => void withWindow((appWindow) => appWindow.close())}
        >
          <X className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}
