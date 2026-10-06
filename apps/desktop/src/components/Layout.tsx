import { NavLink, Outlet } from "react-router-dom";
import { Moon, Sun } from "lucide-react";
import { AppTitleBar } from "@/components/AppTitleBar";
import { toggleTheme, useTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { to: "/setup", label: "设置" },
  { to: "/datasets", label: "数据集" },
  { to: "/runs/new", label: "新建评测" },
  { to: "/history", label: "历史" },
  { to: "/compare", label: "对比" },
];

export function Layout() {
  const theme = useTheme();

  return (
    <div className="flex h-screen flex-col">
      <AppTitleBar />
      <div className="flex min-h-0 flex-1">
        <aside className="flex w-44 flex-col border-r bg-card">
          <div className="px-4 py-4">
            <div className="text-base font-bold">OnprsBench</div>
            <div className="text-xs text-muted-foreground">本地 LLM 评测实验室</div>
          </div>
          <nav className="flex-1 space-y-1 px-2">
            {NAV_ITEMS.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  cn(
                    "block rounded-md px-3 py-2 text-sm transition-colors",
                    isActive ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:bg-accent/50"
                  )
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
          <div className="border-t p-2">
            <button
              type="button"
              className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-sm text-muted-foreground transition-colors hover:bg-accent/50 hover:text-foreground"
              onClick={() => toggleTheme()}
            >
              {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
              {theme === "dark" ? "浅色模式" : "深色模式"}
            </button>
          </div>
        </aside>
        <main className="flex min-h-0 flex-1 flex-col">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
