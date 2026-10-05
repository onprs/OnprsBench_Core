"""从 solver 回答中提取可判定产物（协议第 9 节 Solver 输出契约）。

- issue_resolution：```diff 围栏内的 unified diff 补丁
- code_generation / implementation：```cpp / ```python 围栏内的完整程序

提取失败返回 None，由 verifier 记录为判定事实（patch_applied=false 等），
judge 按 rubric anchors 对相应维度打 0 档。
"""

from __future__ import annotations

import re

_FENCE_RE = re.compile(r"```(?P<lang>[A-Za-z0-9+#-]*)\s*\n(?P<body>.*?)```", re.DOTALL)

_PATCH_LANGS = {"diff", "patch"}
_CPP_LANGS = {"cpp", "c++", "cc", "cxx"}
_PY_LANGS = {"python", "py", "python3"}


def _fenced_blocks(text: str) -> list[tuple[str, str]]:
    return [(m.group("lang").lower(), m.group("body")) for m in _FENCE_RE.finditer(text or "")]


def extract_patch(text: str) -> str | None:
    """提取 unified diff 补丁。优先 ```diff 围栏，退化到裸 diff 文本。"""
    for lang, body in _fenced_blocks(text):
        if lang in _PATCH_LANGS and "---" in body and "+++" in body:
            return body.strip() + "\n"

    # 无围栏兜底：从第一个 diff --git 或 --- a/ 行开始截取
    lines = (text or "").splitlines()
    for marker in ("diff --git ", "--- a/"):
        for idx, line in enumerate(lines):
            if line.startswith(marker):
                candidate = "\n".join(lines[idx:]).strip()
                if "---" in candidate and "+++" in candidate and "@@" in candidate:
                    return candidate + "\n"
    return None


def extract_code(text: str) -> tuple[str, str] | None:
    """提取代码块，返回 (language, code)。language 为 "cpp" 或 "python"。"""
    blocks = _fenced_blocks(text)
    for lang, body in blocks:
        if lang in _CPP_LANGS:
            return "cpp", body
        if lang in _PY_LANGS:
            return "python", body
    # 无语言标注兜底：按内容特征判断
    for lang, body in blocks:
        if lang:
            continue
        if re.search(r"#include\s*<|int\s+main\s*\(", body):
            return "cpp", body
        if re.search(r"^\s*(import\s+\w+|from\s+\w+\s+import|def\s+\w+\()", body, re.MULTILINE):
            return "python", body
    return None
