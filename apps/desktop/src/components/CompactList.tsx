import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";

interface Props {
  items: string[];
  /** 无内容时的占位文本 */
  emptyText?: string;
  className?: string;
}

/**
 * 单行展示一组名称：首项 + 剩余数量。
 * 悬停显示完整列表（原生提示），点击可持久展开（弹层不受表格滚动容器裁剪），
 * 避免多枚徽标平铺撑高表格行。
 */
export function CompactList({ items, emptyText = "—", className }: Props) {
  const [anchor, setAnchor] = useState<{ left: number; top: number } | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!anchor) return;
    function close() {
      setAnchor(null);
    }
    function handlePointerDown(event: MouseEvent) {
      if (triggerRef.current && !triggerRef.current.contains(event.target as Node)) close();
    }
    document.addEventListener("mousedown", handlePointerDown);
    window.addEventListener("scroll", close, true);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      window.removeEventListener("scroll", close, true);
    };
  }, [anchor]);

  if (items.length === 0) {
    return <span className="text-xs text-muted-foreground">{emptyText}</span>;
  }

  const [first, ...rest] = items;

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        title={items.join("\n")}
        onClick={(event) => {
          if (anchor) {
            setAnchor(null);
            return;
          }
          const rect = event.currentTarget.getBoundingClientRect();
          setAnchor({ left: rect.left, top: rect.bottom + 4 });
        }}
        className={cn(
          "flex max-w-[240px] items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs transition-colors hover:bg-accent",
          className
        )}
      >
        <span className="truncate">{first}</span>
        {rest.length > 0 && (
          <span className="shrink-0 text-muted-foreground">+{rest.length}</span>
        )}
      </button>
      {anchor && (
        <div
          className="fixed z-50 min-w-[160px] max-w-[320px] rounded-md border bg-popover p-1.5 text-xs shadow-md"
          style={{ left: anchor.left, top: anchor.top }}
        >
          {items.map((item) => (
            <div key={item} className="truncate px-1.5 py-0.5">
              {item}
            </div>
          ))}
        </div>
      )}
    </>
  );
}
