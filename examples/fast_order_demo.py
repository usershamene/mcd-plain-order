#!/usr/bin/env python3
"""极速点单端到端演示：真实门店数据 + 确定性编排 + 标准化输出。

跑法：python examples/fast_order_demo.py
依赖：scripts/fast_order.py + scripts/render.py + tests/fixtures/05_stores_pickup.json
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))  # 复用 tests/fixtures 的 probe_dir

import fast_order  # noqa: E402
import render  # noqa: E402
import console  # noqa: E402
from fixtures import probe_dir  # noqa: E402

PROBE = probe_dir() / "05_stores_pickup.json"


def load_stores() -> list[dict]:
    with open(PROBE, encoding="utf-8") as f:
        return json.load(f)["data"]


def demo(label: str, context: dict, now: datetime) -> None:
    stores = load_stores()
    plan = fast_order.build_fast_order_plan(stores, context, now)
    msg = render.render_message(
        {
            "greeting": "早高峰，帮你算好最近门店和到店时间",
            "fast_plan": plan,
            "action": {"confirm": True},
        }
    )
    print(f"\n===== 场景：{label} =====")
    print(msg)


def main() -> None:
    console.enable_utf8()
    now = datetime(2026, 10, 11, 9, 0)  # 早餐高峰
    demo("默认（步行到店自取）", {}, now)
    demo("开车（优先得来速）", {"is_driving": True}, now)
    demo("有地址（麦乐送）", {"has_address": True}, now)
    demo("用户指定得来速", {"user_specified": "dt"}, now)


if __name__ == "__main__":
    main()
