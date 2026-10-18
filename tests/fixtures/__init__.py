"""测试固定数据（自包含，已脱敏）。

默认从本目录读取；设置环境变量 ``MCD_PROBE_DIR`` 可指向真实探针目录（作者本地调试用）。
"""
import os
from pathlib import Path


def probe_dir() -> Path:
    override = os.environ.get("MCD_PROBE_DIR")
    return Path(override) if override else Path(__file__).resolve().parent
