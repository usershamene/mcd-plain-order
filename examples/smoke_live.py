#!/usr/bin/env python3
"""真实只读联调烟雾测试（需环境变量 MCD_MCP_TOKEN）。

只做读操作：query-nearby-stores → query-meals → calculate-price。
抓取真实响应形态，校准 workflow._extract_* 字段假设。绝不调用 create-order（写操作）。

安全：本脚本不创建订单、不绑定券、不抽奖。任何写操作都需用户显式二次确认。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import workflow  # noqa: E402
import render  # noqa: E402
import fast_order  # noqa: E402
import location  # noqa: E402
from mcd_client import MCDClient  # noqa: E402


def _brief(obj, keys, n=3):
    if isinstance(obj, list):
        return [_brief(x, keys) for x in obj[:n]]
    if isinstance(obj, dict):
        return {k: obj.get(k) for k in keys if k in obj}
    return obj


def main() -> int:
    token = os.environ.get("MCD_MCP_TOKEN", "").strip()
    if not token:
        print("MCD_MCP_TOKEN 未设置，退出")
        return 2
    client = MCDClient()  # 未传 token 时自动从环境变量 MCD_MCP_TOKEN 读取
    city = os.environ.get("MCD_CITY") or location.detect_city()
    if not city:
        print("未能自动定位城市，且未设置 MCD_CITY，退出")
        return 2
    keyword = os.environ.get("MCD_KEYWORD") or city  # searchType=2 时 city 与 keyword 都必填

    # 1) 附近门店（到店 beType=1）
    try:
        stores_resp = client.call(
            "query-nearby-stores", {"beType": 1, "searchType": 2, "city": city, "keyword": keyword}
        )
    except Exception as exc:  # 含 MCPError / BusinessError / 网络错误
        print(f"[FAIL] query-nearby-stores: {exc}")
        return 1
    stores = workflow._extract_stores(stores_resp)
    print(f"=== query-nearby-stores：返回门店数 {len(stores)}")
    if not stores:
        print("  无门店。raw:", json.dumps(stores_resp, ensure_ascii=False)[:600])
        return 0
    s0 = stores[0]
    print("  门店字段示例:", json.dumps(_brief(s0, (
        "storeCode", "storeName", "distance", "businessStatus",
        "beCode", "reservation", "reservationTimeOptions",
    )), ensure_ascii=False))
    store_code = s0.get("storeCode")

    # 2) 菜单（取真实商品 code，FakeClient 的 M001 等是假的）
    try:
        meals_resp = client.call(
            "query-meals", {"storeCode": store_code, "beType": 1, "orderType": 1}
        )
    except Exception as exc:
        print(f"[FAIL] query-meals: {exc}")
        return 1
    data = meals_resp.get("data", {})
    meals = data.get("meals")  # 真实形态：{code: {name,currentPrice,...}}，code 是键
    print(f"=== query-meals：data 顶层键 {list(data.keys())}；meals 类型 {type(meals).__name__}")
    # 选简单单品（避开需配选的套餐/三件套），保证 calculate-price 能正常出价
    simple_kw = ("可乐", "派", "薯条", "鸡块", "圆筒", "玉米杯", "麦辣鸡腿堡")
    items = []
    if isinstance(meals, dict):
        for code, info in meals.items():
            name = info.get("name", "")
            if any(k in name for k in simple_kw):
                items.append({"productCode": code, "quantity": 1, "name": name})
            if len(items) >= 2:
                break
    if not items and isinstance(meals, dict):  # 兜底：取前两个
        for code, info in list(meals.items())[:2]:
            items.append({"productCode": code, "quantity": 1, "name": info.get("name", "")})
    print("  选用 items:", json.dumps(items, ensure_ascii=False))

    # 3) 算价（只读）
    try:
        price_resp = client.call(
            "calculate-price",
            {"storeCode": store_code, "beType": 1, "orderType": 1, "items": items},
        )
    except Exception as exc:
        print(f"[FAIL] calculate-price: {exc}")
        return 1
    pdata = price_resp.get("data", {})
    print("=== calculate-price raw 关键字段 ===")
    # 打印除 productList 外的全部字段（productList 可能很大）
    dump = {k: v for k, v in pdata.items() if k != "productList"}
    print(json.dumps(dump, ensure_ascii=False))
    print("  takeWayList:", json.dumps(pdata.get("takeWayList"), ensure_ascii=False))

    # 4) 用 workflow 提取器验证真实形态
    try:
        tw = workflow._extract_take_way_code(price_resp)
        print("  [OK] _extract_take_way_code ->", tw)
    except Exception as exc:
        print("  [MISMATCH] _extract_take_way_code:", exc)
    try:
        pr = workflow._extract_price(price_resp)
        print("  [OK] _extract_price ->", pr)
    except Exception as exc:
        print("  [MISMATCH] _extract_price:", exc)

    # 5) 端到端只读编排（pickup, confirmed=False）—— 验证 router 归一化 + workflow 提取 + render 整体
    try:
        out = workflow.run_fast_order(client, {}, items, keyword=keyword, confirmed=False)
        print("=== workflow.run_fast_order(pickup, 只读预览) ===")
        print(render.render_message(out))
    except Exception as exc:
        print(f"[FAIL] workflow pickup 只读: {exc}")

    # 6) 得来速 beCode 端到端校验（只读）：beType=5 必须返回 beCode，
    #    且用该 beCode 算价不能 600048/60002（验证真实配对）。
    try:
        dt_resp = client.call(
            "query-nearby-stores", {"beType": 5, "searchType": 2, "city": city, "keyword": keyword}
        )
        dt_stores = workflow._extract_stores(dt_resp)
        if not dt_stores:
            print("=== DT query-nearby-stores：无门店返回")
        else:
            dt_store = fast_order.select_nearest_store(dt_stores, prefer_dt=True)
            dt_store_code = dt_store.get("storeCode")
            dt_be = dt_store.get("beCode")
            if not dt_be:
                print("=== DT [FAIL] beCode 缺失：query-nearby-stores(beType=5) 未返回 beCode，下单将 60002")
            else:
                dt_meals = client.call(
                    "query-meals",
                    {"storeCode": dt_store_code, "beType": 5, "orderType": 1, "beCode": dt_be},
                ).get("data", {}).get("meals", {})
                dt_items = []
                if isinstance(dt_meals, dict):
                    for code, info in dt_meals.items():
                        name = info.get("name", "")
                        if any(k in name for k in simple_kw):
                            dt_items.append({"productCode": code, "quantity": 1})
                        if len(dt_items) >= 1:
                            break
                if not dt_items:
                    print(f"=== DT beCode={dt_be} 但门店无可用简单单品，跳过算价")
                else:
                    dt_price = client.call(
                        "calculate-price",
                        {
                            "storeCode": dt_store_code,
                            "beType": 5,
                            "orderType": 1,
                            "beCode": dt_be,
                            "items": dt_items,
                        },
                    )
                    dt_pr = workflow._extract_price(dt_price)
                    print(
                        f"=== DT [OK] beCode={dt_be} 配对成功，算价 ¥{dt_pr['pay']}"
                        f"（beType=5 + beCode 无 600048/60002）"
                    )
    except Exception as exc:
        print(f"[FAIL] DT beCode 验证: {exc}")

    # 保存原始响应供人工校准
    out = {"stores_resp": stores_resp, "items": items, "price_resp": price_resp}
    dest = ROOT / "examples" / "live_probe.json"
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已保存原始响应到 {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
