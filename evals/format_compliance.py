#!/usr/bin/env python3
"""L4：对一批模型回复统计格式合规率。

输入 JSONL：{"prompt": "...", "reply": "..."} 每行一条。
用法：python evals/format_compliance.py evals/replies.jsonl
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from validate_reply import validate  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print("用法：python evals/format_compliance.py <replies.jsonl>", file=sys.stderr)
        return 2
    rows = [
        json.loads(l)
        for l in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    if not rows:
        print("空输入", file=sys.stderr)
        return 2

    passed = 0
    reasons: dict[str, int] = {}
    for i, r in enumerate(rows, 1):
        report = validate(r.get("reply", ""))
        if report["ok"]:
            passed += 1
        else:
            for e in report["errors"]:
                reasons[e] = reasons.get(e, 0) + 1
        flag = "PASS" if report["ok"] else "FAIL"
        tail = "" if report["ok"] else " -> " + "; ".join(report["errors"])
        print(f"[{flag}] #{i} {str(r.get('prompt', ''))[:30]}{tail}")

    print(f"\n合规 {passed}/{len(rows)} ({passed / len(rows):.0%})")
    for reason, n in sorted(reasons.items(), key=lambda x: -x[1]):
        print(f"  {n}x {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
