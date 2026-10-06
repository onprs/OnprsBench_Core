"""FastAPI 应用入口。本地优先：默认只监听 127.0.0.1。

启动方式：
    python -m app.main            # 开发
    uvicorn app.main:app          # 通用
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import analytics, datasets, providers, runs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    import asyncio

    from .db import run_migrations
    from .services.runner import recover_interrupted_runs, run_manager

    run_migrations()
    # 上次进程崩溃/被终止时遗留的 running 状态在此恢复，避免 Run 永久卡死
    recover_interrupted_runs()
    run_manager.bind_loop(asyncio.get_running_loop())
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="OnprsBench Server", version="0.1.0", lifespan=lifespan)

    # 本地桌面应用：sidecar 与 vite dev server 跨端口访问
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:14200", "tauri://localhost", "http://tauri.localhost"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(providers.router)
    app.include_router(datasets.router)
    app.include_router(runs.router)
    app.include_router(analytics.router)
    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")


if __name__ == "__main__":
    main()
