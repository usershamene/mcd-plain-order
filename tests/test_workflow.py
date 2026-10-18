#!/usr/bin/env python3
"""workflow.py 集成测试：用 FakeClient 回放真实探针 + 模拟计价/下单，确定性跑通整条链路。

不依赖真实 Token。FakeClient 模拟 tools-reference / be_type_matrix 记载的响应形态，
专门验证编排层的真实风险点：beCode 配对、takeWayCode 来源、reservationDate 处理、orderId 不生成。
"""
from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # 让 fixtures 可导入

import workflow  # noqa: E402
import render  # noqa: E402
from fixtures import probe_dir  # noqa: E402

PROBE = probe_dir()

WINDOW = "早餐(06:14至10:15)，午餐(10:44至14:15)，下午茶(14:44至16:45)，夜市(17:14至21:45)，宵夜(22:14至22:46)"


def load_stores_raw() -> list[dict]:
    with open(PROBE / "05_stores_pickup.json", encoding="utf-8") as f:
        return json.load(f)["data"]


class FakeClient:
    """模拟 MCDClient.call。记录调用，回放探针/模拟响应。"""

    def __init__(self):
        self.calls = []

    def call(self, name: str, args: dict | None = None) -> dict:
        self.calls.append((name, args or {}))
        if name == "query-nearby-stores":
            stores = load_stores_raw()
            # 得来速(beType=5)响应须带 beCode，否则会触发 60002
            if (args or {}).get("beType") == 5:
                stores = [{**s, "beCode": "DT_BE_CODE"} for s in stores]
            return {"success": True, "code": 200, "data": stores}
        if name == "delivery-query-stores":
            # 麦乐送(beType=2) / 团餐(beType=6) 共用此接口，beCode 按 beType 区分，
            # 以验证"团餐用的是 GROUP_BE_CODE、麦乐送用的是 DL_BE_CODE"。
            be_code = "GROUP_BE_CODE" if (args or {}).get("beType") == 6 else "DL_BE_CODE"
            return {
                "success": True,
                "code": 200,
                "data": [
                    {
                        "storeCode": "D1",
                        "storeName": "麦乐送测试店",
                        "distance": 1200,
                        "businessStatus": True,
                        "reservation": True,
                        "beCode": be_code,
                        "reservationTimeOptions": [
                            {"date": "2026-10-11", "today": True, "reservationOptionText": WINDOW}
                        ],
                    }
                ],
            }
        if name == "query-meal-assistance":
            # 团餐专项：返回可选助餐服务列表，gmServiceCode 须来自这里。
            # 默认选中 GMS_001（selected=True）；另给一个未选中的 GMS_002 验证"优先取选中项"。
            return {
                "success": True,
                "code": 200,
                "data": {
                    "mealAssistanceItems": [
                        {
                            "enable": True,
                            "gmServiceCode": "GMS_001",
                            "gmServiceName": "团餐标准助餐",
                            "selected": True,
                            "serviceItems": ["专人配送", "餐具齐备"],
                            "unusableReason": None,
                        },
                        {
                            "enable": True,
                            "gmServiceCode": "GMS_002",
                            "gmServiceName": "团餐尊享助餐",
                            "selected": False,
                            "serviceItems": ["专人配送", "现场服务"],
                            "unusableReason": None,
                        },
                    ]
                },
            }
        if name == "calculate-price":
            # 真实 MCP 金额以「分」为单位，且优先用 originalPrice / price（非 payPrice）。
            # 这里用 3300/2700 表示 ¥33/¥27，下游 _extract_price 会 /100 还原，测试断言不变。
            return {
                "success": True,
                "code": 200,
                "data": {
                    "originalPrice": 3300,
                    "price": 2700,
                    "takeWayList": [{"code": "TW_CODE_1"}],
                },
            }
        if name == "create-order":
            return {"success": True, "code": 200, "data": {"orderId": "ORDER_ABC_123"}}
        raise AssertionError(f"FakeClient 未模拟工具：{name}")

    def called(self, name: str) -> list[tuple]:
        return [c for c, _ in self.calls if c == name]


ITEMS = [{"code": "M001", "qty": 1, "name": "板烧鸡腿堡套餐"}]
NOW = datetime(2026, 10, 11, 9, 0)


class TestPickupFlow(unittest.TestCase):
    def test_preview_not_writing(self):
        c = FakeClient()
        out = workflow.run_fast_order(c, {}, ITEMS, now=NOW, confirmed=False)
        self.assertIn("fast_plan", out)
        self.assertIn("order", out)
        self.assertIn("action", out)
        self.assertNotIn("receipt", out)
        self.assertEqual(c.called("create-order"), [])  # 预览不写

    def test_preview_price_three_line(self):
        c = FakeClient()
        out = workflow.run_fast_order(c, {}, ITEMS, now=NOW, confirmed=False)
        msg = render.render_message(out)
        self.assertIn("原价 ¥33", msg)
        self.assertIn("实付 ¥27", msg)

    def test_confirm_places_order_with_reservation(self):
        c = FakeClient()
        out = workflow.run_fast_order(c, {}, ITEMS, now=NOW, confirmed=True)
        self.assertIn("receipt", out)
        self.assertEqual(out["order_id"], "ORDER_ABC_123")
        # create-order 被调用，且带走店自取参数（无 beCode、带 reservationDate）
        create_args = [a for n, a in c.calls if n == "create-order"][0]
        self.assertNotIn("beCode", create_args)
        self.assertIn("reservationDate", create_args)
        self.assertIn("takeWayCode", create_args)
        self.assertEqual(create_args["takeWayCode"], "TW_CODE_1")  # 必须来自计价

    def test_take_way_code_from_price_not_invented(self):
        c = FakeClient()
        workflow.run_fast_order(c, {}, ITEMS, now=NOW, confirmed=True)
        price_args = [a for n, a in c.calls if n == "calculate-price"][0]
        self.assertNotIn("takeWayCode", price_args)  # 计价阶段还不带 takeWayCode

    def test_injected_stores_skip_query(self):
        """传入 stores 时不应再查 query-nearby-stores（真实环境两次查询可能返回不同门店）。"""
        c = FakeClient()
        workflow.run_fast_order(c, {}, ITEMS, now=NOW, confirmed=False, stores=load_stores_raw())
        self.assertEqual(c.called("query-nearby-stores"), [])

    def test_city_forwarded_to_store_query(self):
        """city 必须透传到 query-nearby-stores，否则真实接口报 600058（城市/关键词为空）。"""
        c = FakeClient()
        workflow.run_fast_order(c, {}, ITEMS, now=NOW, confirmed=False, city="示例市")
        args = [a for n, a in c.calls if n == "query-nearby-stores"][0]
        self.assertEqual(args.get("city"), "示例市")

    def test_injected_stores_use_first_store(self):
        """注入 stores 时按调用方顺序取第一家（不再按距离重选），保证选品/计价同店。"""
        base = load_stores_raw()[0]
        caller_choice = {**base, "storeCode": "CALLER_CHOICE", "distance": 9999}
        nearest = {**base, "storeCode": "NEAREST", "distance": 1}
        c = FakeClient()
        out = workflow.run_fast_order(
            c, {}, ITEMS, now=NOW, confirmed=True, stores=[caller_choice, nearest]
        )
        self.assertEqual(out["order_id"], "ORDER_ABC_123")
        create_args = [a for n, a in c.calls if n == "create-order"][0]
        self.assertEqual(create_args["storeCode"], "CALLER_CHOICE")

    def test_price_call_has_no_reservation_date(self):
        """calculate-price 不传 reservationDate（那是 create-order 的参数，传了会让部分商品 60006）。"""
        c = FakeClient()
        workflow.run_fast_order(c, {}, ITEMS, now=NOW, confirmed=True)
        price_args = [a for n, a in c.calls if n == "calculate-price"][0]
        self.assertNotIn("reservationDate", price_args)
        # 但 create-order 必须带 reservationDate（到店预约场景）
        create_args = [a for n, a in c.calls if n == "create-order"][0]
        self.assertIn("reservationDate", create_args)


class TestPriceExtraction(unittest.TestCase):
    """真实 MCP 金额单位是「分」，_extract_price 必须换算成「元」。"""

    def test_cents_to_yuan(self):
        out = workflow._extract_price(
            {"data": {"originalPrice": 1950, "price": 1450, "discount": 500}}
        )
        self.assertEqual(out, {"original": 19.5, "discount": 5.0, "pay": 14.5})

    def test_zero_discount_stays_zero(self):
        out = workflow._extract_price({"data": {"originalPrice": 1800, "price": 1800, "discount": 0}})
        self.assertEqual(out["discount"], 0.0)

    def test_missing_amount_raises(self):
        with self.assertRaises(workflow.WorkflowError):
            workflow._extract_price({"data": {"originalPrice": 0, "price": 0}})


class TestDtFlow(unittest.TestCase):
    def test_dt_passes_be_code_and_take_way(self):
        c = FakeClient()
        out = workflow.run_fast_order(c, {"is_driving": True}, ITEMS, now=NOW, confirmed=True)
        self.assertEqual(out["order_id"], "ORDER_ABC_123")
        create_args = [a for n, a in c.calls if n == "create-order"][0]
        self.assertEqual(create_args["beCode"], "DT_BE_CODE")  # 来自 query-nearby-stores
        self.assertEqual(create_args["takeWayCode"], "TW_CODE_1")


class TestDeliveryFlow(unittest.TestCase):
    def test_delivery_requires_address(self):
        c = FakeClient()
        with self.assertRaises(workflow.WorkflowError):
            workflow.run_fast_order(c, {"has_address": True}, ITEMS, now=NOW, confirmed=True)

    def test_delivery_passes_be_code_no_take_way(self):
        c = FakeClient()
        out = workflow.run_fast_order(
            c, {"has_address": True, "address_id": "ADDR1"}, ITEMS, now=NOW, confirmed=True
        )
        create_args = [a for n, a in c.calls if n == "create-order"][0]
        self.assertEqual(create_args["beCode"], "DL_BE_CODE")
        self.assertNotIn("takeWayCode", create_args)  # 外送不传
        self.assertEqual(create_args["addressId"], "ADDR1")
        self.assertNotIn("reservationDate", create_args)

    def test_delivery_preview_message(self):
        c = FakeClient()
        out = workflow.run_fast_order(
            c, {"has_address": True, "address_id": "ADDR1"}, ITEMS, now=NOW, confirmed=False
        )
        msg = render.render_message(out)
        self.assertIn("麦乐送", msg)
        self.assertIn("预计 17 分钟送达", msg)  # 1200/70≈17.1→17


class TestErrorPaths(unittest.TestCase):
    def test_no_store_returns_error_plan(self):
        c = FakeClient()
        closed = [{**s, "businessStatus": False} for s in load_stores_raw()]
        c.call = lambda n, a=None: (  # 临时让 query-nearby-stores 返回全关店
            {"success": True, "code": 200, "data": closed}
            if n == "query-nearby-stores"
            else FakeClient.call(c, n, a)
        )
        out = workflow.run_fast_order(c, {}, ITEMS, now=NOW, confirmed=True)
        self.assertIn("error", out["fast_plan"])
        self.assertEqual(c.called("create-order"), [])  # 无店不下单


class TestGmServiceExtraction(unittest.TestCase):
    """团餐 gmServiceCode 只能来自 query-meal-assistance，须正确解析。"""

    def test_picks_selected_first(self):
        resp = {"data": {"mealAssistanceItems": [
            {"selected": False, "gmServiceCode": "A"},
            {"selected": True, "gmServiceCode": "B"},
        ]}}
        self.assertEqual(workflow._extract_gm_service_code(resp), "B")

    def test_falls_back_to_enabled(self):
        resp = {"data": {"mealAssistanceItems": [
            {"selected": False, "enable": True, "gmServiceCode": "C"},
        ]}}
        self.assertEqual(workflow._extract_gm_service_code(resp), "C")

    def test_missing_items_raises(self):
        with self.assertRaises(workflow.WorkflowError):
            workflow._extract_gm_service_code({"data": {"mealAssistanceItems": []}})

    def test_missing_code_raises(self):
        resp = {"data": {"mealAssistanceItems": [{"selected": True, "gmServiceName": "x"}]}}
        with self.assertRaises(workflow.WorkflowError):
            workflow._extract_gm_service_code(resp)


class TestGroupFlow(unittest.TestCase):
    """企业团餐（beType=6 / orderType=2）完整路径。需真实 addressId；此处用 FakeClient 离线回放。"""

    def test_group_requires_address(self):
        c = FakeClient()
        with self.assertRaises(workflow.WorkflowError):
            workflow.run_fast_order(c, {"user_specified": "group"}, ITEMS, now=NOW, confirmed=True)

    def test_group_full_flow_fields(self):
        c = FakeClient()
        out = workflow.run_fast_order(
            c, {"user_specified": "group", "address_id": "ADDR_G"}, ITEMS, now=NOW, confirmed=True
        )
        self.assertEqual(out["order_id"], "ORDER_ABC_123")
        create_args = [a for n, a in c.calls if n == "create-order"][0]
        # 团餐三要素必须齐：beCode(来自 delivery-query-stores beType=6) + gmServiceCode + addressId
        self.assertEqual(create_args["beCode"], "GROUP_BE_CODE")
        self.assertEqual(create_args["gmServiceCode"], "GMS_001")
        self.assertEqual(create_args["addressId"], "ADDR_G")
        # 团餐(orderType=2)不传 takeWayCode / reservationDate
        self.assertNotIn("takeWayCode", create_args)
        self.assertNotIn("reservationDate", create_args)

    def test_group_meal_assistance_before_price(self):
        c = FakeClient()
        workflow.run_fast_order(
            c, {"user_specified": "group", "address_id": "ADDR_G"}, ITEMS, now=NOW, confirmed=True
        )
        names = [n for n, _ in c.calls]
        self.assertIn("query-meal-assistance", names)
        self.assertIn("calculate-price", names)
        self.assertLess(
            names.index("query-meal-assistance"),
            names.index("calculate-price"),
            "query-meal-assistance 必须在 calculate-price 之前调用",
        )

    def test_group_price_carries_gm_service_code(self):
        c = FakeClient()
        workflow.run_fast_order(
            c, {"user_specified": "group", "address_id": "ADDR_G"}, ITEMS, now=NOW, confirmed=True
        )
        price_args = [a for n, a in c.calls if n == "calculate-price"][0]
        self.assertEqual(price_args["gmServiceCode"], "GMS_001")
        self.assertEqual(price_args["beCode"], "GROUP_BE_CODE")
        self.assertNotIn("addressId", price_args)  # 计价只读，不需要 addressId

    def test_group_preview_not_writing(self):
        c = FakeClient()
        out = workflow.run_fast_order(
            c, {"user_specified": "group", "address_id": "ADDR_G"}, ITEMS, now=NOW, confirmed=False
        )
        self.assertIn("fast_plan", out)
        self.assertIn("order", out)
        self.assertIn("action", out)
        self.assertNotIn("receipt", out)
        self.assertEqual(c.called("create-order"), [])  # 预览不写
        msg = render.render_message(out)
        self.assertIn("企业团餐", msg)
        self.assertIn("预计 17 分钟送达", msg)  # 1200/70≈17.1→17


class FakeClientBig(FakeClient):
    """calculate-price 返回超大额（¥400），用于验证 amount_limit 闸门。"""

    def call(self, name: str, args: dict | None = None) -> dict:
        if name == "calculate-price":
            return {
                "success": True,
                "code": 200,
                "data": {
                    "originalPrice": 50000,
                    "price": 40000,
                    "takeWayList": [{"code": "TW_CODE_1"}],
                },
            }
        return super().call(name, args)


class TestAmountLimit(unittest.TestCase):
    """amount_limit 覆盖参数：默认 ¥200 上限，显式放宽可放行大单（如团餐 ≥¥300）。"""

    def test_over_limit_blocked_by_default(self):
        c = FakeClientBig()
        with self.assertRaises(workflow.guard.GuardError):
            workflow.run_fast_order(
                c, {"has_address": True, "address_id": "ADDR1"}, ITEMS, now=NOW, confirmed=True
            )

    def test_over_limit_allowed_when_raised(self):
        c = FakeClientBig()
        out = workflow.run_fast_order(
            c,
            {"has_address": True, "address_id": "ADDR1"},
            ITEMS,
            now=NOW,
            confirmed=True,
            amount_limit=1000,
        )
        self.assertEqual(out["order_id"], "ORDER_ABC_123")

    def test_group_over_default_raised(self):
        """团餐(group)默认上限自动抬高，¥400 大单不再被 200 默认误拦。"""
        c = FakeClientBig()
        out = workflow.run_fast_order(
            c, {"user_specified": "group", "address_id": "ADDR_G"}, ITEMS, now=NOW, confirmed=True
        )
        self.assertEqual(out["order_id"], "ORDER_ABC_123")


if __name__ == "__main__":
    unittest.main(verbosity=2)
