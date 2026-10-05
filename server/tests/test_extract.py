"""Solver 回答产物提取测试（协议第 9 节输出契约）。"""

from __future__ import annotations

from app.verifier.extract import extract_code, extract_patch

PATCH_SAMPLE = """\
我看出了问题所在，这是修复补丁：

```diff
diff --git a/src/calc.py b/src/calc.py
index 1111111..2222222 100644
--- a/src/calc.py
+++ b/src/calc.py
@@ -1,2 +1,2 @@
-def add(a, b): return a - b
+def add(a, b): return a + b
```

说明：把减号改为加号。
"""


def test_extract_patch_from_diff_fence() -> None:
    patch = extract_patch(PATCH_SAMPLE)
    assert patch is not None
    assert "diff --git" in patch
    assert "-def add(a, b): return a - b" in patch
    assert "+def add(a, b): return a + b" in patch


def test_extract_patch_bare_diff() -> None:
    text = "说明文字\ndiff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new\n"
    patch = extract_patch(text)
    assert patch is not None
    assert patch.startswith("diff --git")


def test_extract_patch_missing() -> None:
    assert extract_patch("没有任何补丁的回答") is None
    assert extract_patch("```python\nprint('hi')\n```") is None


def test_extract_code_cpp() -> None:
    text = "思路：贪心。\n\n```cpp\n#include <bits/stdc++.h>\nint main(){return 0;}\n```\n"
    result = extract_code(text)
    assert result is not None
    lang, code = result
    assert lang == "cpp"
    assert "int main" in code


def test_extract_code_python() -> None:
    text = "```python\nimport sys\nprint(sys.stdin.read())\n```"
    result = extract_code(text)
    assert result is not None
    assert result[0] == "python"


def test_extract_code_unfenced_language_hint() -> None:
    text = "```\n#include <iostream>\nint main(){}\n```"
    result = extract_code(text)
    assert result is not None
    assert result[0] == "cpp"


def test_extract_code_missing() -> None:
    assert extract_code("纯文本回答，没有代码") is None
