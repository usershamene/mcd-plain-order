#!/usr/bin/env python3
"""L2：校验一条回复是否符合 references/output-format.md。

用法：
  python scripts/validate_reply.py --file reply.txt
  echo "..." | python scripts/validate_reply.py

输出 JSON：{"ok": bool, "errors": [...], "warnings": [...]}；不合规退出码 1。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PRIMARY_ICONS = ("🍔", "🏪", "🍟", "✅", "⚠️")
BANNED_WORDS = ("折扣", "抵扣", "核销", "随心配", "券包", "限时", "仅剩", "快抢")
MAX_LINES = 18
WARN_LINES = 14
# 回执首行的门店编码，如「（3330492）」或「（S0001）」
_STORE_CODE = re.compile(r"（[A-Za-z]?\d{3,}）")


def validate(text: str) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return {"ok": False, "errors": ["空消息"], "warnings": []}

    heads = [ln for ln in lines if ln.lstrip().startswith(PRIMARY_ICONS)]
    if not heads:
        errors.append("缺少主块标题（🍔/🏪/🍟/✅/⚠️）")
    if len(heads) > 1:
        warnings.append(f"有 {len(heads)} 个主块，建议只保留一个")

    for w in BANNED_WORDS:
        if w in text:
            errors.append(f"出现禁用词「{w}」")

    if "原价" in text and "实付" not in text:
        errors.append("有原价但缺实付")
    if "优惠" in text and "-¥" not in text:
        errors.append("优惠行格式应为「优惠 -¥X」")

    if "已下单" in text:
        head = next((ln for ln in lines if ln.lstrip().startswith("✅")), lines[0])
        if not _STORE_CODE.search(head):
            errors.append("下单回执首行缺少门店编码（如「门店名（3330492）」）")

    if len(lines) > MAX_LINES:
        errors.append(f"消息 {len(lines)} 行，超过 {MAX_LINES} 行预算")
    elif len(lines) > WARN_LINES:
        warnings.append(f"消息 {len(lines)} 行，偏长")

    return {"ok": not errors, "errors": errors, "warnings": warnings}


def main() -> int:
    ap = argparse.ArgumentParser(description="校验回复格式")
    ap.add_argument("--file", default=None)
    args = ap.parse_args()
    text = Path(args.file).read_text(encoding="utf-8") if args.file else sys.stdin.read()
    report = validate(text)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
