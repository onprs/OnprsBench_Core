import { useCallback, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

interface ConfirmOptions {
  title: string;
  description?: string;
  confirmText?: string;
}

interface ConfirmState extends ConfirmOptions {
  resolve: (value: boolean) => void;
}

/**
 * 应用内确认对话框（替代浏览器原生 confirm）。
 *
 * 用法：
 *   const { confirm, confirmElement } = useConfirm();
 *   if (await confirm({ title: "删除 Provider？" })) { ... }
 *   return <>{confirmElement}</>;
 */
export function useConfirm() {
  const [state, setState] = useState<ConfirmState | null>(null);

  const confirm = useCallback(
    (options: ConfirmOptions) =>
      new Promise<boolean>((resolve) => {
        setState({ ...options, resolve });
      }),
    []
  );

  const settle = (value: boolean) => {
    state?.resolve(value);
    setState(null);
  };

  const confirmElement = (
    <Dialog open={state !== null} onOpenChange={(open) => !open && settle(false)}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>{state?.title}</DialogTitle>
          {state?.description && <DialogDescription>{state.description}</DialogDescription>}
        </DialogHeader>
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={() => settle(false)}>
            取消
          </Button>
          <Button variant="destructive" onClick={() => settle(true)}>
            {state?.confirmText ?? "确认"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );

  return { confirm, confirmElement };
}
