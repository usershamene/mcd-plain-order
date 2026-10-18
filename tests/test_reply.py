#!/usr/bin/env python3
"""输出格式模板 + 校验器单测。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import render  # noqa: E402
import validate_reply  # noqa: E402


class TestRenderTemplates(unittest.TestCase):
    def test_stores_single_line_each(self):
        out = render.render_stores([
            {"storeName": "示例得来速餐厅", "storeCode": "S0001", "distance": 605,
             "businessStartTime": "06:00", "businessEndTime": "23:00", "reservation": True},
        ])
        self.assertIn("🏪 附近门店（1 家", out)
        self.assertIn("示例得来速餐厅（S0001 · 605 米 · 06:00–23:00 · 可预约）", out)

    def test_menu_with_store(self):
        out = render.render_menu(
            [{"name": "灰焰圆筒", "price": "5", "tag": "上新"}],
            {"storeName": "示例得来速餐厅", "storeCode": "S0001"},
        )
        self.assertIn("🍟 菜单 · 示例得来速餐厅 · S0001", out)
        self.assertIn("· 灰焰圆筒 ¥5（上新）", out)

    def test_compare_labels(self):
        out = render.render_compare(
            [{"name": "A 组合", "pay": "20.9"}, {"name": "B 组合", "pay": "21.9"}],
            mode="到店自取",
        )
        self.assertIn("🍔 方案对比（到店自取）", out)
        self.assertIn("A. A 组合 · 实付 ¥20.9", out)
        self.assertIn("B. B 组合 · 实付 ¥21.9", out)

    def test_receipt_has_store_code_and_order(self):
        out = render.render_receipt({
            "storeName": "示例得来速餐厅", "storeCode": "S0001",
            "orderId": "ORDER123", "payUrl": "https://pay.example",
            "payDeadline": "2026-10-18 21:30", "cancellableMin": 10, "pay": "20.9",
        })
        self.assertIn("✅ 已下单 · 示例得来速餐厅（S0001）", out)
        self.assertIn("· 订单号：ORDER123", out)
        self.assertIn("· 付款：https://pay.example", out)
        self.assertIn("（10 分钟内可取消）", out)
        self.assertIn("· 实付 ¥20.9", out)

    def test_message_assembles_new_types(self):
        out = render.render_message({
            "stores": [{"storeName": "示例得来速餐厅", "storeCode": "S0001", "distance": 605,
                        "reservation": True}],
            "action": {"confirm": False, "note": "选一家"},
        })
        self.assertIn("🏪 附近门店", out)
        self.assertIn("👉 选一家", out)


class TestValidator(unittest.TestCase):
    def test_compliant_passes(self):
        text = (
            "🏪 附近门店（1 家 · 按距离）\n"
            "· 示例得来速餐厅（S0001 · 605 米 · 06:00–23:00 · 可预约）\n\n"
            "👉 选一家，我帮你看菜单"
        )
        self.assertTrue(validate_reply.validate(text)["ok"])

    def test_banned_word_fails(self):
        text = "🍔 为你推荐（到店自取）\n· 套餐 ×1\n原价 ¥33\n实付 ¥27\n限时优惠"
        rep = validate_reply.validate(text)
        self.assertFalse(rep["ok"])
        self.assertTrue(any("限时" in e for e in rep["errors"]))

    def test_receipt_without_store_code_fails(self):
        text = "✅ 已下单 · 示例得来速餐厅\n· 订单号：ORDER123\n· 实付 ¥20.9"
        rep = validate_reply.validate(text)
        self.assertFalse(rep["ok"])
        self.assertTrue(any("门店编码" in e for e in rep["errors"]))

    def test_missing_pay_fails(self):
        text = "🍔 为你推荐（到店自取）\n· 套餐 ×1\n原价 ¥33"
        rep = validate_reply.validate(text)
        self.assertFalse(rep["ok"])
        self.assertTrue(any("实付" in e for e in rep["errors"]))

    def test_too_long_fails(self):
        text = "🍔 为你推荐\n" + "\n".join(f"· 商品{i} ×1" for i in range(20))
        rep = validate_reply.validate(text)
        self.assertFalse(rep["ok"])
        self.assertTrue(any("行预算" in e for e in rep["errors"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
