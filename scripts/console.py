"""控制台编码：Windows 下把 stdout/stderr 切到 UTF-8，避免中文乱码。

实测：DSH/PowerShell 里 `python -c "... location.detect_city()"` 会输出 `����`，
需要 `PYTHONIOENCODING=utf-8` 或本模块的 `enable_utf8()`。
"""
from __future__ import annotations

import sys


def enable_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:  # 非文本流 / 老 Python 时忽略
            pass
