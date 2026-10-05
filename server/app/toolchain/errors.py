"""工具链供给异常。"""

from __future__ import annotations


class ToolchainUnavailable(RuntimeError):
    """所需工具链无法供给（无网络、平台不支持等）。调用方应优雅降级。"""
