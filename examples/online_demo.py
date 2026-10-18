#!/usr/bin/env python3
"""极速点单端到端演示（Token 就绪 / 离线回放双模式）。

自动切换：
- 若环境变量 MCD_MCP_TOKEN 已设置 → 用真实 MCDClient 跑「只读预览」（confirmed=False），
  绝不自动下单（防误操作）。live 模式对每个取餐方式各自预热：查门店 → 抓菜单 → 挑当前可
  正常计价的商品，再把同一份门店列表交给 workflow，保证"选品门店 == 计价门店"。
- 若未设置 → 用 tests 里的 FakeClient 回放真实探针 + 模拟计价/下单，确定性跑通整条链路。

安全约定（防"演示变生产"）：
1. live 模式默认只跑预览（只读：query-nearby-stores / query-meals / calculate-price 等，不写）。
2. 真下单（--really-order）必须人工显式确认，且只针对「到店自取」单一场景，
   避免把演示脚本当成批量下单工具造成多笔真实订单。
3. 字段以 workflow.py 内 _extract_* 为准（已据真实响应校准：productCode、分单位、takeWayList）。

真实环境踩过的坑（已在代码里规避）：
- query-nearby-stores 的 searchType=2 要求 city 与 keyword 都非空，否则 600058；
- 同一入参两次调用可能返回不同门店 → 选品与计价门店必须一致，故注入 stores；
- calculate-price 不传 reservationDate（那是 create-order 的参数，传了会让部分商品 60006）；
- 金额单位是「分」，items 字段是 productCode/quantity。

跑法：
  python examples/online_demo.py                # 离线回放；若设了 Token 则只读预览
  python examples/online_demo.py --really-order  # 仅当已设 Token 时，对「到店自取」真下单
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import fast_order  # noqa: E402
import render  # noqa: E402
import workflow  # noqa: E402
import location  # noqa: E402

NOW = datetime(2026, 10, 11, 9, 0)
ITEMS = [
    {"code": "M001", "qty": 1, "name": "板烧鸡腿堡套餐"},
    {"code": "M002", "qty": 1, "name": "薯条"},
]

# 演示场景：取餐方式 → context
SCENARIOS = [
    ("到店自取", {}),
    ("开车得来速", {"is_driving": True}),
    ("麦乐送", {"has_address": True, "address_id": "ADDR1"}),
    ("企业团餐", {"user_specified": "group", "has_address": True, "address_id": "ADDR_G"}),
]

_SIMPLE_KW = ("可乐", "派", "薯条", "鸡块", "圆筒", "玉米杯", "麦辣鸡腿堡")


def make_client(live: bool):
    """live=True 且设了 Token → 真实 MCDClient；否则 FakeClient 离线回放。"""
    if live:
        from mcd_client import MCDClient

        return MCDClient()
    from tests.test_workflow import FakeClient

    return FakeClient()


def _priceable(client, store_code: str, codes: list[str], be_type: int, order_type: int, be_code: str | None = None) -> bool:
    """整组商品能否一起正常计价（排除售罄/不可售，避免演示撞 60006）。
    beType=5/6 时必传 beCode（真实接口 600048）。"""
    if not codes:
        return False
    args = {"storeCode": store_code, "beType": be_type, "orderType": order_type,
            "items": [{"productCode": c, "quantity": 1} for c in codes]}
    if be_code:
        args["beCode"] = be_code
    try:
        r = client.call("calculate-price", args)
        return bool(r.get("data", {}).get("productList"))
    except Exception:
        return False


def prepare_live(client, context: dict, city: str, keyword: str):
    """按取餐方式各自预热：查门店 → 选最近营业店 → 抓菜单 → 挑可计价商品。
    返回 (stores, items)；外送需真实地址，演示跳过（返回 (None, None)）。"""
    method = fast_order.choose_pickup_method(context)
    if method in ("delivery", "group"):
        return None, None  # 外送/团餐需真实 addressId（写操作），演示不做
    be_type = 5 if method == "dt" else 1
    order_type = 1
    try:
        stores = workflow._extract_stores(
            client.call("query-nearby-stores", {"beType": be_type, "searchType": 2, "city": city, "keyword": keyword})
        )
        store = fast_order.select_nearest_store(stores, prefer_dt=(method == "dt"))
        if not store:
            return stores, None
        store_code = store.get("storeCode")
        be_code = store.get("beCode") if method == "dt" else None  # 得来速查询菜单/计价必传 beCode
        meals_args = {"storeCode": store_code, "beType": be_type, "orderType": order_type}
        if be_code:
            meals_args["beCode"] = be_code
        meals = client.call("query-meals", meals_args).get("data", {}).get("meals", {})
        candidates = []
        if isinstance(meals, dict):
            for code, info in meals.items():
                name = info.get("name", "")
                if any(k in name for k in _SIMPLE_KW):
                    candidates.append((code, name))
                if len(candidates) >= 8:
                    break
        ok = [(c, n) for c, n in candidates if _priceable(client, store_code, [c], be_type, order_type, be_code)]
        if len(ok) >= 2 and _priceable(client, store_code, [ok[0][0], ok[1][0]], be_type, order_type, be_code):
            chosen = [ok[0], ok[1]]
        elif ok:
            chosen = [ok[0]]
        else:
            return stores, None
        return stores, [{"productCode": c, "quantity": 1, "name": n} for c, n in chosen]
    except Exception:
        return None, None


def demo(label: str, context: dict, items, client, *, live: bool, really_order: bool, now, stores=None) -> None:
    print(f"\n===== {label} =====")
    # 第一阶段：方案 + 算价预览（不写）
    try:
        preview = workflow.run_fast_order(client, context, items, now=now, confirmed=False, stores=stores)
    except Exception as exc:
        print(f"— 预览失败（只读调用抛错，不影响其它场景）—\n  {exc}")
        return
    print("— 预览（未确认，不会下单）—")
    print(render.render_message(preview))
    if preview.get("fast_plan", {}).get("error"):
        print("（方案存在错误，已软提示，不继续）")
        return

    if really_order:
        # 真下单：仅当用户显式确认（命令行 --really-order）才走到这
        try:
            done = workflow.run_fast_order(client, context, items, now=now, confirmed=True, stores=stores)
        except Exception as exc:
            print(f"\n— 下单失败（已按安全约定中止，未造成订单）—\n  {exc}")
            return
        print("\n— 已真下单 —")
        print(render.render_message(done))
        print(f"[orderId] {done.get('order_id')}")
    elif live:
        print("\n（live 模式仅只读预览；要真下单请加 --really-order）")
    else:
        # 离线：模拟确认下单，验证整条链路
        done = workflow.run_fast_order(client, context, items, now=NOW, confirmed=True, stores=stores)
        print("\n— 确认后（已下单，模拟）—")
        print(render.render_message(done))
        print(f"[orderId] {done.get('order_id')}")


def main() -> None:
    parser = argparse.ArgumentParser(description="极速点单端到端演示")
    parser.add_argument(
        "--really-order",
        action="store_true",
        help="仅当已设置 MCD_MCP_TOKEN 时，对「到店自取」真下单（需显式确认）",
    )
    args = parser.parse_args()

    token = os.environ.get("MCD_MCP_TOKEN", "").strip()
    live = bool(token)

    if live:
        city = os.environ.get("MCD_CITY") or location.detect_city()
        if not city:
            print("未能自动定位城市，且未设置 MCD_CITY；跳过 live 演示。")
            return
        keyword = os.environ.get("MCD_KEYWORD") or city
        print(f"检测到 MCD_MCP_TOKEN：以真实麦当劳 MCP 只读预览模式运行（城市：{city}，不自动下单）。")
        client = make_client(True)
        now = datetime.now()  # 预约必须未来时间，否则真实接口报 600053
        for label, context in SCENARIOS:
            stores, items = prepare_live(client, context, city, keyword)
            if not items:
                print(f"\n===== {label} =====\n  （未预热到可计价商品：外送需真实地址，或当前门店无可用商品，跳过）")
                continue
            really = args.really_order and label == "到店自取"  # 真下单只允许单一场景
            demo(label, context, items, client, live=True, really_order=really, now=now, stores=stores)
    else:
        print("未检测到 MCD_MCP_TOKEN：以 FakeClient 离线回放模式运行（基于真实探针数据）。")
        client = make_client(False)
        for label, context in SCENARIOS:
            demo(label, context, ITEMS, client, live=False, really_order=False, now=NOW, stores=None)


if __name__ == "__main__":
    main()
