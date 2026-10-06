import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

/**
 * 渲染异常兜底：任何页面组件抛错时显示错误信息与重试入口，
 * 避免 React 卸载整棵组件树后出现空白（深色主题下表现为黑屏）。
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("[ui] 渲染异常:", error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-3 bg-background p-6 text-center text-foreground">
        <div className="text-lg font-semibold">页面渲染出错</div>
        <p className="max-w-xl break-words text-sm text-muted-foreground">{this.state.error.message}</p>
        <div className="flex gap-2">
          <button
            type="button"
            className="rounded-md border px-3 py-1.5 text-sm transition-colors hover:bg-accent"
            onClick={() => this.setState({ error: null })}
          >
            重试
          </button>
          <button
            type="button"
            className="rounded-md border px-3 py-1.5 text-sm transition-colors hover:bg-accent"
            onClick={() => window.location.reload()}
          >
            重新加载
          </button>
        </div>
      </div>
    );
  }
}
