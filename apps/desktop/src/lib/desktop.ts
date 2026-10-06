/**
 * 桌面壳能力封装：系统目录选择与拖入事件。
 * 浏览器开发模式下这些能力不可用，调用方据此显示降级提示。
 */

export function isDesktopApp(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

/** 打开系统目录选择对话框；取消或非桌面环境返回 null。 */
export async function pickDirectory(): Promise<string | null> {
  if (!isDesktopApp()) return null;
  const { open } = await import("@tauri-apps/plugin-dialog");
  const selected = await open({ directory: true, multiple: false, title: "选择数据集目录" });
  return typeof selected === "string" ? selected : null;
}

/** 选择数据集发布包（.tar.gz / .tgz / .tar）；取消或非桌面环境返回 null。 */
export async function pickArchiveFile(): Promise<string | null> {
  if (!isDesktopApp()) return null;
  const { open } = await import("@tauri-apps/plugin-dialog");
  const selected = await open({
    multiple: false,
    title: "选择数据集压缩包",
    filters: [{ name: "数据集压缩包", extensions: ["gz", "tgz", "tar"] }],
  });
  return typeof selected === "string" ? selected : null;
}

/** 监听桌面壳拖入事件，返回取消监听的函数（非桌面环境为空实现）。 */
export async function listenDirectoryDrop(options: {
  onDrop: (paths: string[]) => void;
  onDragStateChange?: (active: boolean) => void;
}): Promise<() => void> {
  if (!isDesktopApp()) return () => {};
  const { getCurrentWebview } = await import("@tauri-apps/api/webview");
  return getCurrentWebview().onDragDropEvent((event) => {
    const type = event.payload.type;
    if (type === "enter" || type === "over") {
      options.onDragStateChange?.(true);
      return;
    }
    if (type === "leave") {
      options.onDragStateChange?.(false);
      return;
    }
    if (type === "drop") {
      options.onDragStateChange?.(false);
      if (event.payload.paths.length > 0) options.onDrop(event.payload.paths);
    }
  });
}
