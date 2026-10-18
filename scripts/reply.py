#!/usr/bin/env python3
"""L1 合规入口：把结构化 result 渲染成最终消息。

用法：
  python scripts/reply.py --json-file result.json
  python scripts/reply.py --json '{"order": {...}, "action": {"confirm": true}}'
  echo '{...}' | python scripts/reply.py --check

--check 会顺带跑 validate_reply；不合规退出码 1。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import console  # noqa: E402
import render  # noqa: E402
from validate_reply import validate  # noqa: E402


def main() -> int:
    console.enable_utf8()
    ap = argparse.ArgumentParser(description="把 result JSON 渲染成最终消息")
    ap.add_argument("--json", default=None)
    ap.add_argument("--json-file", default=None)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    if args.json_file:
        raw = Path(args.json_file).read_text(encoding="utf-8")
    elif args.json:
        raw = args.json
    else:
        raw = sys.stdin.read()

    text = render.render_message(json.loads(raw))
    print(text)

    if args.check:
        report = validate(text)
        if not report["ok"]:
            print("[validate] FAIL: " + "; ".join(report["errors"]), file=sys.stderr)
            return 1
        if report["warnings"]:
            print("[validate] WARN: " + "; ".join(report["warnings"]), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
