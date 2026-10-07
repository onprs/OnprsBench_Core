import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";

interface Props {
  items: string[];
  /** 卡片内展示的详情（与 items 一一对应，缺省时回退到 items 本身） */
  details?: string[];
  /** 无内容时的占位文本 */
  emptyText?: string;
  className?: string;
}

/**
 * 单行展示一组名称：首项 + 剩余数量。
 * 悬停与点击展示同一个卡片（fixed 定位，不受表格滚动容器裁剪）：
 * - 悬停展开，移开自动收起（允许鼠标移入卡片查看）；
 * - 点击固定住卡片，再次点击或点击外部收起。
 * 避免多枚徽标平铺撑高表格行。
 */
export function CompactList({ items, details, emptyText = "—", className }: Props) {
  const [anchor, setAnchor] = useState<{ left: number; top: number } | null>(null);
  const [pinned, setPinned] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const hoverRef = useRef(false);
  const pinnedRef = useRef(false);
  const closeTimer = useRef<number | null>(null);

  function cancelClose() {
    if (closeTimer.current !== null) {
      window.clearTimeout(closeTimer.current);
      closeTimer.current = null;
    }
  }

  function open() {
    cancelClose();
    const rect = triggerRef.current?.getBoundingClientRect();
    if (rect) setAnchor({ left: rect.left, top: rect.bottom + 4 });
  }

  function scheduleClose() {
    cancelClose();
    closeTimer.current = window.setTimeout(() => {
      closeTimer.current = null;
      if (!pinnedRef.current && !hoverRef.current) setAnchor(null);
    }, 120);
  }

  useEffect(() => () => cancelClose(), []);

  useEffect(() => {
    if (!anchor) return;
    function handlePointerDown(event: MouseEvent) {
      const insideTrigger = triggerRef.current?.contains(event.target as Node) ?? false;
      if (!insideTrigger) {
        pinnedRef.current = false;
        setPinned(false);
        setAnchor(null);
      }
    }
    function handleScroll() {
      pinnedRef.current = false;
      setPinned(false);
      setAnchor(null);
    }
    document.addEventListener("mousedown", handlePointerDown);
    window.addEventListener("scroll", handleScroll, true);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      window.removeEventListener("scroll", handleScroll, true);
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
        onMouseEnter={() => {
          hoverRef.current = true;
          open();
        }}
        onMouseLeave={() => {
          hoverRef.current = false;
          scheduleClose();
        }}
        onClick={() => {
          pinnedRef.current = !pinnedRef.current;
          setPinned(pinnedRef.current);
          if (pinnedRef.current) open();
          else if (!hoverRef.current) setAnchor(null);
        }}
        className={cn(
          "flex max-w-[240px] items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs transition-colors hover:bg-accent",
          pinned && "border-primary/60 bg-accent",
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
          onMouseEnter={() => {
            hoverRef.current = true;
            cancelClose();
          }}
          onMouseLeave={() => {
            hoverRef.current = false;
            scheduleClose();
          }}
          className="fixed z-50 min-w-[160px] max-w-[360px] rounded-md border bg-popover p-1.5 text-xs shadow-md"
          style={{ left: anchor.left, top: anchor.top }}
        >
          {items.map((item, index) => (
            <div key={item} className="break-words px-1.5 py-0.5">
              {details?.[index] ?? item}
            </div>
          ))}
        </div>
      )}
    </>
  );
}
