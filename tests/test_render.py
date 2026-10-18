#!/usr/bin/env python3
"""render.py 单测：输出格式是确定性合同，必须逐段断言。

验证四件事：每段格式正确、数字/大白话符合规范、空段被跳过、整体顺序固定。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import render  # noqa: E402


class TestGreeting(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(render.render_greeting(None), "")
        self.assertEqual(render.render_greeting(""), "")

    def test_formatted(self):
        self.assertEqual(render.render_greeting("早，通勤路上？"), "👋 早，通勤路上？")


class TestOrder(unittest.TestCase):
    def test_full_order_three_line_price(self):
        order = {
            "take_mode": "到店自取",
            "eta": "预计 12:30 到店即取",
            "items": [{"name": "大脆鸡扒麦满分组合", "qty": 1}, {"name": "热豆浆", "qty": 1}],
            "price": {"original": "13.5", "discount": "5.4", "discount_reason": "早餐卡", "pay": "8.1"},
        }
        out = render.render_order(order)
        self.assertIn("🍔 为你推荐（到店自取 · 预计 12:30 到店即取）", out)
        self.assertIn("· 大脆鸡扒麦满分组合 ×1", out)
        self.assertIn("原价 ¥13.5", out)
        self.assertIn("优惠 -¥5.4（早餐卡）", out)
        self.assertIn("实付 ¥8.1", out)

    def test_no_price_section_when_missing(self):
        out = render.render_order({"items": [{"name": "薯条", "qty": 2}]})
        self.assertIn("· 薯条 ×2", out)
        self.assertNotIn("原价", out)

    def test_empty(self):
        self.assertEqual(render.render_order({}), "")


class TestNew(unittest.TestCase):
    def test_liked_flag_inline(self):
        out = render.render_new([{"name": "蓝莓爆爆珠麦旋风", "price": "16", "tag": "上新", "liked": True}])
        self.assertIn("🆕 新品", out)
        self.assertIn("· 蓝莓爆爆珠麦旋风 ¥16（上新） · 你喜欢", out)

    def test_no_liked_label(self):
        out = render.render_new([{"name": "多汁柠柠", "price": "12", "tag": "清爽上新"}])
        self.assertIn("🆕 新品", out)
        self.assertNotIn("你喜欢", out)

    def test_empty(self):
        self.assertEqual(render.render_new([]), "")


class TestCoupon(unittest.TestCase):
    def test_with_urgency(self):
        out = render.render_coupon([{"title": "中薯买一送一", "urgency": "剩 82 分钟"}])
        self.assertIn("🎫 券提醒", out)
        self.assertIn("· 中薯买一送一 · 剩 82 分钟", out)

    def test_liked_flag_inline(self):
        out = render.render_coupon([{"title": "中薯条买一送一", "urgency": "剩 82 分钟", "liked": True}])
        self.assertIn("· 中薯条买一送一 · 剩 82 分钟 · 你喜欢", out)

    def test_empty(self):
        self.assertEqual(render.render_coupon([]), "")


class TestPoints(unittest.TestCase):
    def test_with_near_target(self):
        out = render.render_points({"balance": 1280, "near": {"name": "卡通玩偶", "gap": 720}})
        self.assertEqual(out, "⭐ 积分 · 当前 1280 分，还差 720 分换「卡通玩偶」")

    def test_reachable(self):
        out = render.render_points(
            {"balance": 180.7, "near": {"name": "板烧鸡腿堡两件套", "gap": 0, "reachable": True}}
        )
        self.assertEqual(out, "⭐ 积分 · 当前 180.7 分，可换「板烧鸡腿堡两件套」")

    def test_integer_balance_no_decimal(self):
        out = render.render_points({"balance": 180.0, "near": None})
        self.assertEqual(out, "⭐ 积分 · 当前 180 分")

    def test_balance_only(self):
        out = render.render_points({"balance": 1280})
        self.assertEqual(out, "⭐ 积分 · 当前 1280 分")

    def test_missing_balance(self):
        self.assertEqual(render.render_points({}), "")


class TestAction(unittest.TestCase):
    def test_confirm_with_cancellable(self):
        out = render.render_action({"confirm": True, "cancellable_min": 10})
        self.assertIn("👉 确认下单？", out)
        self.assertIn("下单后 10 分钟内可取消", out)

    def test_empty(self):
        self.assertEqual(render.render_action({}), "")


class TestAssemble(unittest.TestCase):
    def test_order_and_skip_empty_sections(self):
        result = {
            "order": {
                "take_mode": "麦乐送",
                "items": [{"name": "巨无霸", "qty": 1}],
                "price": {"original": "24", "discount": "0", "pay": "24"},
            },
            # reminders 下 new/coupon/points 全部缺失 → 不应出现对应段
            "action": {"confirm": True},
        }
        out = render.render_message(result)
        self.assertIn("🍔 为你推荐（麦乐送）", out)
        self.assertIn("👉 确认下单？", out)
        self.assertNotIn("🆕", out)
        self.assertNotIn("🎫", out)
        self.assertNotIn("⭐", out)

    def test_full_assembly_order(self):
        result = {
            "greeting": "早",
            "order": {"take_mode": "到店自取", "items": [{"name": "咖啡", "qty": 1}],
                      "price": {"original": "18", "discount": "3", "reason": "券", "pay": "15"}},
            "reminders": {
                "new": [{"name": "灰焰圆筒", "price": "5", "tag": "上新", "liked": True}],
                "coupon": [{"title": "早餐券", "urgency": "剩 30 分钟"}],
                "points": {"balance": 500},
            },
            "action": {"confirm": True},
        }
        out = render.render_message(result)
        # 顺序：问候 → 点单 → 新品 → 券 → 积分 → 行动
        idx_g, idx_o = out.index("👋"), out.index("🍔")
        idx_new, idx_c = out.index("🆕"), out.index("🎫")
        idx_p, idx_a = out.index("⭐"), out.index("👉")
        self.assertTrue(idx_g < idx_o < idx_new < idx_c < idx_p < idx_a)
        # 段间空行分隔
        self.assertIn("\n\n", out)


class TestFastPlan(unittest.TestCase):
    def _plan(self, method="pickup", adjusted=False):
        return {
            "store": {"storeCode": "S0001", "storeName": "示例得来速餐厅", "distance": 605},
            "method": method,
            "eta_minutes": 9,
            "reservation": {
                "date": "2026-10-11",
                "reservationDate": "2026-10-11 09:14",
                "window": "早餐",
                "adjusted": adjusted,
            },
        }

    def test_pickup_header(self):
        out = render.render_fast_plan(self._plan("pickup"))
        self.assertIn("🍔 极速点单（到店自取 · 预计 9 分钟到店）", out)

    def test_dt_header(self):
        out = render.render_fast_plan(self._plan("dt"))
        self.assertIn("🍔 极速点单（得来速 · 预计 9 分钟到店）", out)

    def test_delivery_label(self):
        out = render.render_fast_plan(self._plan("delivery"))
        self.assertIn("预计 9 分钟送达", out)
        # 麦乐送不挂门店预约时段
        self.assertNotIn("预约到店", out)

    def test_store_and_reservation_lines(self):
        out = render.render_fast_plan(self._plan())
        self.assertIn("· 最近门店：示例得来速餐厅（605 米）", out)
        self.assertIn("· 预约到店：2026-10-11 09:14（早餐时段，到店即取）", out)

    def test_adjusted_note(self):
        out = render.render_fast_plan(self._plan(adjusted=True))
        self.assertIn("已顺延至本时段起点", out)

    def test_empty_on_error(self):
        self.assertEqual(render.render_fast_plan({}), "")
        # 无可用门店时软提示，不静默丢块
        err_out = render.render_fast_plan({"error": "附近无营业且支持预约的门店"})
        self.assertIn("🍔 极速点单", err_out)
        self.assertIn("附近无营业且支持预约的门店", err_out)

    def test_no_reservation_note(self):
        plan = {
            "store": {"storeName": "门店A", "distance": 100},
            "method": "pickup",
            "eta_minutes": 2,
            "reservation": None,
        }
        out = render.render_fast_plan(plan)
        self.assertIn("今日预约时段已过，可直接到店", out)
        self.assertNotIn("预约到店", out)

    def test_in_message_assembly(self):
        result = {"fast_plan": self._plan(), "action": {"confirm": True}}
        out = render.render_message(result)
        self.assertIn("🍔 极速点单", out)
        self.assertIn("👉 确认下单？", out)
        self.assertIn("\n\n", out)

    def test_detail_mode_under_order(self):
        # 与点单块同框时降级为「📍 取餐安排」子块，不再出现第二个 🍔 极速点单
        result = {"order": {"items": [{"name": "薯条", "qty": 1}]}, "fast_plan": self._plan()}
        out = render.render_message(result)
        self.assertIn("📍 取餐安排", out)
        self.assertNotIn("🍔 极速点单", out)
        self.assertIn("· 最近门店：示例得来速餐厅（605 米）", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
