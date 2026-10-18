#!/usr/bin/env python3
"""各种现象链路自检。

覆盖：极速点单四种形态 + 晚点/无店边界 + 普通推荐点单（三行金额）+
完整四段组装 + 单段提醒 + 个性化过滤/高亮 + 空段跳过。
每条链路独立捕获异常；最后汇总 PASS/FAIL，并校验"你的点单"已从所有输出消失。

跑法：python examples/chain_check.py
"""
from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))  # 复用 tests/fixtures 的 probe_dir

import fast_order  # noqa: E402
import render  # noqa: E402
import newitems  # noqa: E402
import reminders  # noqa: E402
import personalize  # noqa: E402
import console  # noqa: E402
from fixtures import probe_dir  # noqa: E402

console.enable_utf8()
PROBE = probe_dir()


def load_raw(name: str) -> dict:
    with open(PROBE / name, encoding="utf-8") as f:
        return json.load(f)


stores_data = load_raw("05_stores_pickup.json")["data"]
meals_raw = load_raw("07_meals_pickup.json")
account_raw = load_raw("02_my_account.json")
mall_raw = load_raw("11_mall_products.json")
coupons_raw = load_raw("03_my_coupons.json")

NOW_MORNING = datetime(2026, 10, 11, 9, 0)
NOW_LATE = datetime(2026, 10, 11, 23, 0)

results: list[tuple[str, bool, str]] = []


def run(name: str, fn) -> None:
    try:
        out = fn()
        ok = isinstance(out, str)
        results.append((name, ok, out if ok else f"<非字符串返回: {type(out)}>"))
    except Exception:  # noqa: BLE001
        results.append((name, False, traceback.format_exc()))


# —— 极速点单：四种形态 ——
def chain_fast_pickup():
    plan = fast_order.build_fast_order_plan(stores_data, {}, NOW_MORNING)
    return render.render_message(
        {"greeting": "早高峰，帮你算好最近门店", "fast_plan": plan, "action": {"confirm": True}}
    )


def chain_fast_dt_driving():
    plan = fast_order.build_fast_order_plan(stores_data, {"is_driving": True}, NOW_MORNING)
    return render.render_message({"fast_plan": plan, "action": {"confirm": True}})


def chain_fast_delivery():
    plan = fast_order.build_fast_order_plan(stores_data, {"has_address": True}, NOW_MORNING)
    return render.render_message({"fast_plan": plan, "action": {"confirm": True}})


def chain_fast_user_dt():
    plan = fast_order.build_fast_order_plan(stores_data, {"user_specified": "dt"}, NOW_MORNING)
    return render.render_message({"fast_plan": plan, "action": {"confirm": True}})


# —— 极速点单：边界 ——
def chain_fast_late_night():
    # 23:00 门店仍营业，但今日预约时段已全部结束 → reservation=None，应提示"可直接到店"
    plan = fast_order.build_fast_order_plan(stores_data, {}, NOW_LATE)
    assert plan["reservation"] is None, "late night 应无 reservation"
    return render.render_message({"fast_plan": plan, "action": {"confirm": True}})


def chain_fast_no_store():
    # 全部关店 → build 返回 error，render 软提示不丢块
    closed = [{**s, "businessStatus": False} for s in stores_data]
    plan = fast_order.build_fast_order_plan(closed, {}, NOW_MORNING)
    assert "error" in plan, "无店应返回 error"
    return render.render_message({"fast_plan": plan, "action": {"confirm": True}})


# —— 普通推荐点单（三行金额）——
def chain_order_three_line():
    order = {
        "take_mode": "到店自取",
        "eta": "预计 12:30 到店即取",
        "items": [
            {"name": "大脆鸡扒麦满分组合", "qty": 1},
            {"name": "热豆浆", "qty": 1},
        ],
        "price": {"original": "13.5", "discount": "5.4", "discount_reason": "早餐卡", "pay": "8.1"},
    }
    return render.render_message({"order": order, "action": {"confirm": True}})


# —— 完整四段：推荐点单 + 新品 + 券 + 积分 + 行动 ——
def chain_full_four():
    order = {
        "take_mode": "到店自取",
        "items": [{"name": "板烧鸡腿堡套餐", "qty": 1}],
        "price": {"original": "33", "discount": "6", "discount_reason": "券", "pay": "27"},
    }
    new = newitems.new_items_for_user(meals_raw, {})
    coupon = reminders.build_coupon_reminder(coupons_raw, datetime(2026, 10, 11, 9, 7, 38), 1, 120)
    points = reminders.pick_points_target(account_raw, mall_raw)
    return render.render_message(
        {
            "greeting": "早",
            "order": order,
            "reminders": {"new": new, "coupon": coupon, "points": points},
            "action": {"confirm": True},
        }
    )


# —— 仅新品提醒（用户只问"有什么新品"）——
def chain_only_new():
    new = newitems.new_items_for_user(meals_raw, {"likes": ["冰淇淋"], "dislikes": [], "dietary": []})
    return render.render_message({"reminders": {"new": new}, "action": {"confirm": True}})


# —— 仅积分提醒（用户问"我的积分"）——
def chain_only_points():
    points = reminders.pick_points_target(account_raw, mall_raw)
    return render.render_message({"reminders": {"points": points}})


# —— 个性化：不喜欢辣被过滤 + 喜欢冰淇淋被高亮 ——
def chain_personalize():
    profile = {"dislikes": ["辣"], "likes": ["冰淇淋"], "dietary": []}
    recs = [
        {"name": "麦辣鸡腿堡"},
        {"name": "板烧鸡腿堡"},
        {"name": "麦旋风"},
    ]
    kept, removed = personalize.filter_recommendations(recs, profile)
    kept_names = [r["name"] for r in kept]
    assert "麦辣鸡腿堡" not in kept_names, "辣的应被过滤"
    new = newitems.new_items_for_user(meals_raw, profile)
    liked = [n["name"] for n in new if n["liked"]]
    return (
        f"过滤后推荐：{kept_names}\n"
        f"新品中命中'你喜欢'：{liked}\n"
        f"{render.render_new(new)}"
    )


# —— 空问候/空行动：只给方案，跳过空段 ——
def chain_minimal():
    plan = fast_order.build_fast_order_plan(stores_data, {}, NOW_MORNING)
    return render.render_message({"fast_plan": plan})


# —— 仅推荐点单、无行动（如预览，不触发写操作）——
def chain_order_no_action():
    order = {"items": [{"name": "薯条", "qty": 2}]}
    return render.render_message({"order": order})


# —— 券兴趣匹配：用户喜欢猪柳蛋 → 紧迫的厚松饼猪柳蛋券标"你喜欢" ——
def chain_coupon_interest():
    profile = {"likes": ["猪柳蛋"], "dislikes": [], "dietary": []}
    coupon = reminders.build_coupon_reminder(
        coupons_raw, datetime(2026, 10, 11, 9, 7, 38), 1, 120, profile=profile
    )
    return render.render_message({"reminders": {"coupon": coupon}})


if __name__ == "__main__":
    for name, fn in [
        ("极速点单·步行到店自取", chain_fast_pickup),
        ("极速点单·开车得来速", chain_fast_dt_driving),
        ("极速点单·有地址麦乐送", chain_fast_delivery),
        ("极速点单·用户指定得来速", chain_fast_user_dt),
        ("极速点单·深夜(无预约时段)", chain_fast_late_night),
        ("极速点单·无营业门店(边界)", chain_fast_no_store),
        ("普通推荐点单·三行金额", chain_order_three_line),
        ("完整四段·推荐+新品+券+积分", chain_full_four),
        ("仅新品提醒", chain_only_new),
        ("仅积分提醒", chain_only_points),
        ("个性化·过滤辣+高亮冰淇淋", chain_personalize),
        ("券兴趣匹配·喜欢薯条", chain_coupon_interest),
        ("空问候/行动·只给方案", chain_minimal),
        ("仅推荐点单·无行动", chain_order_no_action),
    ]:
        run(name, fn)

    passed = sum(1 for _, ok, _ in results if ok)
    print("=" * 60)
    print(f"链路自检：{passed}/{len(results)} 通过（异常即 FAIL）")
    print("=" * 60)
    leak = False
    for name, ok, out in results:
        print(f"\n[{'PASS' if ok else 'FAIL'}] {name}")
        print("-" * 40)
        print(out)
        if "你的点单" in (out or ""):
            leak = True
            print("⚠️ 检测到旧标题'你的点单'未清理！")
    print("\n" + "=" * 60)
    print("旧标题'你的点单'残留检查：", "发现残留！" if leak else "无残留 ✓")
    print("=" * 60)
