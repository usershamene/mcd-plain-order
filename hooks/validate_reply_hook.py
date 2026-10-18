#!/usr/bin/env python3
"""L3 pre-send hook 示例：校验待发送文本，不合规非 0 退出。

读取：环境变量 MCD_REPLY_TEXT；否则 stdin。
接线方式见 references/enforcement.md（宿主不同，字段不同）。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from validate_reply import validate  # noqa: E402


def main() -> int:
    text = os.environ.get("MCD_REPLY_TEXT")
    if text is None:
        text = sys.stdin.read()
    report = validate(text)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
