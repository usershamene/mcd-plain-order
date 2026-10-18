#!/usr/bin/env python3
"""一站式下单 CLI（推荐入口）。

用法：
  # 只预览（不写）
  python scripts/order.py --method pickup --items 9900016076:1,521951:1 --city 宁波 --keyword 长兴路
  # 用户明确说「下单」后再执行（--confirm 传用户原话）
  python scripts/order.py ... --confirm "下单"

约定：
- Token 只从环境变量 MCD_MCP_TOKEN 读取，**禁止写进命令行**。
- 时间/城市缺省自动获取，不询问用户。
- 参数一律经 workflow / router 构造，**禁止手写 storeCode / items**。
- 指定门店只在查询结果里选，绝不凭印象填 code。
- 失败一律原文输出，并以非 0 退出。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import console  # noqa: E402
import location  # noqa: E402
import workflow  # noqa: E402
from mcd_client import BusinessError, MCDClient, MCPError  # noqa: E402


def parse_items(raw: str) -> list[dict]:
    items = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        code, _, qty = part.partition(":")
        items.append({"code": code.strip(), "qty": int(qty or 1)})
    return items


def main() -> int:
    console.enable_utf8()
    ap = argparse.ArgumentParser(description="麦当劳极速点单（一站式）")
    ap.add_argument("--method", choices=("pickup", "dt", "delivery", "group"), default="pickup")
    ap.add_argument("--items", required=True, help="商品，形如 code:qty,code:qty")
    ap.add_argument("--city", default=None)
    ap.add_argument("--keyword", default=None)
    ap.add_argument("--store-code", default=None, help="指定门店（必须来自查询结果）")
    ap.add_argument("--address-id", default=None)
    ap.add_argument("--confirm", default=None, help="用户在对话里的确认原话（如「下单」）；不传则只预览")
    args = ap.parse_args()

    client = MCDClient()  # Token 只从环境变量读
    city = args.city or location.detect_city()
    if not city and args.method in ("pickup", "dt"):
        print("未能自动定位城市，请传 --city", file=sys.stderr)
        return 2
    keyword = args.keyword or city or ""

    context: dict = {"user_specified": args.method}
    if args.method in ("delivery", "group"):
        if not args.address_id:
            print("麦乐送/团餐需要 --address-id", file=sys.stderr)
            return 2
        context.update({"has_address": True, "address_id": args.address_id})
    elif args.method == "dt":
        context["is_driving"] = True

    stores = None
    if args.store_code and args.method in ("pickup", "dt"):
        # 只在查询结果里选店，绝不凭印象填 code（实测曾把柳西 3330716 当成长兴路 3330492）
        be_type = 5 if args.method == "dt" else 1
        resp = client.call(
            "query-nearby-stores",
            {"beType": be_type, "searchType": 2, "city": city, "keyword": keyword},
        )
        all_stores = workflow._extract_stores(resp)
        chosen = next(
            (s for s in all_stores if str(s.get("storeCode")) == str(args.store_code)), None
        )
        if not chosen:
            print(
                f"门店 {args.store_code} 不在返回列表；可选："
                f"{[s.get('storeCode') for s in all_stores]}",
                file=sys.stderr,
            )
            return 2
        stores = [chosen] + [s for s in all_stores if s is not chosen]

    try:
        out = workflow.run_fast_order(
            client,
            context,
            parse_items(args.items),
            city=city,
            keyword=keyword,
            confirmed=bool(args.confirm),
            user_confirmation=args.confirm,
            stores=stores,
        )
    except Exception as exc:  # noqa: BLE001  顶层入口：业务/闸门/网络错误统一原文输出
        print(f"失败：{exc}", file=sys.stderr)
        return 1

    plan = out.get("fast_plan", {})
    store = plan.get("store", {})
    print(
        f"门店：{store.get('storeName')}"
        f"（storeCode={store.get('storeCode')}，{store.get('distance')} 米）"
    )

    order = out.get("order")
    if order:
        print(f"取餐方式：{order.get('take_mode')}｜{order.get('eta')}")
        for it in order.get("items", []):
            print(f"  · {it.get('name')} ×{it.get('qty')}")
        price = order.get("price", {})
        print(f"原价 ¥{price.get('original')}")
        if float(price.get("discount", 0) or 0) > 0:
            print(f"优惠 ¥{price.get('discount')}")
        print(f"实付 ¥{price.get('pay')}")

    if out.get("order_id"):
        print(f"orderId：{out['order_id']}")
        print(out.get("receipt", ""))
    else:
        print('（仅预览，未下单。用户明确说「下单」后，加 --confirm "下单" 再执行。）')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
