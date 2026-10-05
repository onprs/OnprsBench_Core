import { NavLink, Outlet } from "react-router-dom";
import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { to: "/setup", label: "设置" },
  { to: "/datasets", label: "数据集" },
  { to: "/runs/new", label: "新建 Run" },
  { to: "/history", label: "历史" },
  { to: "/compare", label: "对比" },
];

export function Layout() {
  return (
    <div className="flex h-screen">
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
      </aside>
      <main className="flex-1 overflow-auto p-6">
        <Outlet />
      </main>
    </div>
  );
}
