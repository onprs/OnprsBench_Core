import { CircleHelp } from "lucide-react";
import { cn } from "@/lib/utils";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";

/** 小问号帮助图标：解释性文本统一收纳到悬浮提示中，不直接铺在界面上。 */
export function HelpTip({ text, className }: { text: string; className?: string }) {
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            tabIndex={-1}
            className={cn(
              "inline-flex items-center text-muted-foreground/60 transition-colors hover:text-muted-foreground",
              className
            )}
          >
            <CircleHelp className="h-3.5 w-3.5" />
            <span className="sr-only">说明</span>
          </button>
        </TooltipTrigger>
        <TooltipContent>{text}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
