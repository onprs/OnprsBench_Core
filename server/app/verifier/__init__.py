"""程序判定（verifier）：在框架自带环境中执行任务的 verify 契约，产出判定事实。

两类契约：
- issue_resolution（SWE 类）：应用 solver 补丁 → 跑 FAIL_TO_PASS / PASS_TO_PASS 测试
- code_generation（竞赛类）：编译/运行 solver 代码 → 官方样例 + 生成器应力测试

判定事实（Verifier Facts）作为 immutable raw facts 落库，并注入 judge prompt；
凡 rubric 维度声明"程序 verifier 判定"的，judge 必须以判定事实为依据。

工具链无法供给（无网络、平台不支持等）时抛出 ToolchainUnavailable，
由 runner 捕获并把该次判定标记为 unavailable，不阻塞评分流程。
"""

from .base import VerifyOutcome
from .service import verify_task

__all__ = ["VerifyOutcome", "verify_task"]
