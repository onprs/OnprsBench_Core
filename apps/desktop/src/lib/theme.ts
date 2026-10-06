/**
 * 主题管理：浅色 / 深色切换。
 *
 * 主题状态保存在 localStorage，并在渲染前应用到根元素；
 * 桌面应用下同时同步窗口背景色，避免调整窗口大小时露出与主题不符的底色。
 */

import { useEffect, useState } from "react";
import { isDesktopApp } from "@/lib/desktop";

export type Theme = "light" | "dark";

const STORAGE_KEY = "onprsbench.theme";
const WINDOW_BACKGROUND: Record<Theme, string> = {
  light: "#FFFFFF",
  dark: "#09090B",
};

const listeners = new Set<(theme: Theme) => void>();

export function readStoredTheme(): Theme {
  try {
    return localStorage.getItem(STORAGE_KEY) === "light" ? "light" : "dark";
  } catch {
    return "dark";
  }
}

export function getTheme(): Theme {
  return document.documentElement.classList.contains("dark") ? "dark" : "light";
}

/** 应用主题到根元素、页面背景与桌面窗口背景。 */
export function applyTheme(theme: Theme, options?: { persist?: boolean }): void {
  const root = document.documentElement;
  root.classList.toggle("dark", theme === "dark");
  root.style.backgroundColor = WINDOW_BACKGROUND[theme].toLowerCase();
  if (options?.persist) {
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      // 存储不可用时仅影响下次启动的默认值
    }
  }
  void syncWindowBackground(theme);
  void syncWindowBorder(theme);
  listeners.forEach((listener) => listener(theme));
}

/** 切换并持久化主题，返回切换后的主题。 */
export function toggleTheme(): Theme {
  const next: Theme = getTheme() === "dark" ? "light" : "dark";
  applyTheme(next, { persist: true });
  return next;
}

/** React 订阅：主题变化时触发重渲染。 */
export function useTheme(): Theme {
  const [theme, setTheme] = useState<Theme>(getTheme);
  useEffect(() => {
    listeners.add(setTheme);
    return () => {
      listeners.delete(setTheme);
    };
  }, []);
  return theme;
}

async function syncWindowBackground(theme: Theme): Promise<void> {
  if (!isDesktopApp()) return;
  try {
    const { getCurrentWindow } = await import("@tauri-apps/api/window");
    await getCurrentWindow().setBackgroundColor(WINDOW_BACKGROUND[theme]);
  } catch {
    // 窗口背景同步失败不影响页面主题
  }
}

/** 同步窗口 DWM 边框色（Windows 11）；旧系统不支持时忽略。 */
async function syncWindowBorder(theme: Theme): Promise<void> {
  if (!isDesktopApp()) return;
  try {
    const { invoke } = await import("@tauri-apps/api/core");
    await invoke("set_window_border_color", { color: WINDOW_BACKGROUND[theme] });
  } catch {
    // 不支持边框色设置时忽略
  }
}
