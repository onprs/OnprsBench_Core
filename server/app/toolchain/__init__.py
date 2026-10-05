"""工具链自动供给。

原则：程序判定所需环境由框架自带，不要求用户预装。
- uv：首次使用时自动下载静态二进制（系统 PATH 已有的优先复用）。
- Python 解释器与依赖：经 uv 下载独立构建并创建虚拟环境。
- 源码仓库：按 commit 下载 GitHub 归档（tarball），本地缓存，不依赖 git。
- C/C++ 编译器：优先系统编译器；Windows 缺失时自动下载便携 MinGW。

任何一步无法供给都抛出 ToolchainUnavailable，由 verifier 捕获并优雅降级
（该任务退化为纯文本评审，结果中标注），绝不阻塞整个 Run。
"""

from .errors import ToolchainUnavailable

__all__ = ["ToolchainUnavailable"]
