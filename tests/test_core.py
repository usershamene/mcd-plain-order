#!/usr/bin/env python3
"""核心确定性逻辑单测。

只测「必须显式代码实现」的部分：路由表、券窗口判定、写操作闸门。
这些逻辑的共同点是——错了会造成真实损失（下单失败、券白扔、误扣费），
所以必须有测试，且断言的是具体值与错误类型，不是「不报错」。

运行：python tests/test_core.py
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import coupons  # noqa: E402
import guard  # noqa: E402
import router  # noqa: E402


# ---------- 券 fixture ----------
# 结构完全来自实测（真实 Token 抓取），仅日期做了整理。
BREAKFAST_COUPON = {
    "title": "巧克力味厚松饼猪柳蛋套餐",
    "tradeStartDateTime": "2026-10-11 05:00:00",
    "tradeEndDateTime": "2026-10-11 10:29:59",
    "tradeTimePeriods": [{"startTime": "05:00:00", "endTime": "10:29:59"}],
    "isAllTradeTime": 0,
    "weekdays": [1],  # 实测：1 = 周日
    "beTypes": [1, 5],
    "orderTypes": [1],
    "totalCount": 1,
    "totalAvailableCount": 1,
    "onlyAppCanRedeem": False,
    "onlyPosCanRedeem": False,
}

# 跨周一到周五的多日券（实测同款：中薯条买一送一）
WEEKDAY_COUPON = {
    "title": "中薯条买一送一",
    "tradeStartDateTime": "2026-10-12 10:30:00",
    "tradeEndDateTime": "2026-10-16 23:59:59",
    "tradeTimePeriods": [],
    "isAllTradeTime": 1,
    "weekdays": [2, 3, 4, 5, 6],  # 周一至周五
    "beTypes": [1, 2, 5],
    "orderTypes": [1, 2],
    "totalAvailableCount": 1,
    "onlyAppCanRedeem": False,
    "onlyPosCanRedeem": False,
}


class TestRouter(unittest.TestCase):
    """路由表：错了就会拿到 60002。"""

    def test_resolve_matches_live_matrix(self):
        expected = {
            router.TakeMode.PICKUP: (1, 1, False),
            router.TakeMode.DT: (5, 1, True),
            router.TakeMode.DELIVERY: (2, 2, True),
            router.TakeMode.GROUP: (6, 2, True),
        }
        for mode, (be_type, order_type, needs_code) in expected.items():
            plan = router.resolve(mode)
            self.assertEqual((plan.be_type, plan.order_type), (be_type, order_type), mode)
            self.assertEqual(plan.needs_be_code, needs_code, mode)

    def test_pickup_must_not_send_be_code(self):
        """到店自传 beCode 会报错——必须在本地就拦住。"""
        plan = router.resolve(router.TakeMode.PICKUP)
        with self.assertRaises(router.RouteError):
            router.build_args(plan, store_code="3330492", be_code="333054841")
        args = router.build_args(plan, store_code="3330492")
        self.assertNotIn("beCode", args)
        self.assertEqual(args["beType"], 1)
        self.assertEqual(args["orderType"], 1)

    def test_items_normalized_to_product_code(self):
        """真实接口 items 字段是 productCode/quantity（不是 code/qty）——边界处必须转换。"""
        plan = router.resolve(router.TakeMode.PICKUP)
        args = router.build_args(plan, store_code="1", items=[{"code": "A1", "qty": 2, "name": "可乐"}])
        self.assertEqual(args["items"], [{"productCode": "A1", "quantity": 2}])
        # 已是 MCP 形态时保持原样
        args2 = router.build_args(plan, store_code="1", items=[{"productCode": "B2", "quantity": 1}])
        self.assertEqual(args2["items"], [{"productCode": "B2", "quantity": 1}])

    def test_delivery_requires_be_code_and_address(self):
        plan = router.resolve(router.TakeMode.DELIVERY)
        with self.assertRaises(router.RouteError):
            router.build_args(plan, store_code="1", address_id="a1")  # 缺 beCode
        with self.assertRaises(router.RouteError):
            router.build_args(plan, store_code="1", be_code="b1")  # 缺 addressId

    def test_group_requires_gm_service_code(self):
        plan = router.resolve(router.TakeMode.GROUP)
        with self.assertRaises(router.RouteError):
            router.build_args(plan, store_code="1", be_code="b1", address_id="a1")
        args = router.build_args(
            plan, store_code="1", be_code="b1", address_id="a1", gm_service_code="g1"
        )
        self.assertEqual((args["beType"], args["orderType"]), (6, 2))

    def test_dt_rejects_delivery_only_params(self):
        """得来速不支持 addressId / remark，静默忽略会让用户以为生效了。"""
        plan = router.resolve(router.TakeMode.DT)
        for bad in ({"address_id": "a1"}, {"remark": "无接触配送"}):
            with self.assertRaises(router.RouteError):
                router.build_args(plan, store_code="1", be_code="b1", **bad)

    def test_reservation_date_format_enforced(self):
        plan = router.resolve(router.TakeMode.PICKUP)
        with self.assertRaises(router.RouteError):
            router.build_args(plan, store_code="1", reservation_date="2026/10/12 08:00")
        args = router.build_args(plan, store_code="1", reservation_date="2026-10-12 08:00")
        self.assertEqual(args["reservationDate"], "2026-10-12 08:00")

    def test_choose_take_mode(self):
        self.assertIs(router.choose_take_mode(has_address=True), router.TakeMode.DELIVERY)
        self.assertIs(router.choose_take_mode(has_address=False), router.TakeMode.PICKUP)
        self.assertIs(
            router.choose_take_mode(has_address=True, prefer=router.TakeMode.DT),
            router.TakeMode.DT,
        )

    def test_unknown_mode_rejected(self):
        with self.assertRaises(router.RouteError):
            router.resolve("teleport")


class TestCouponWindow(unittest.TestCase):
    """券窗口：这是「权益提醒」的命门，错一分钟都是真金白银。"""

    def test_weekday_mapping_matches_reality(self):
        # 实测结论：1=周日、2=周一 … 7=周六
        self.assertEqual(coupons.to_mcd_weekday(datetime(2026, 10, 11)), 1)  # 周日
        self.assertEqual(coupons.to_mcd_weekday(datetime(2026, 10, 12)), 2)  # 周一
        self.assertEqual(coupons.to_mcd_weekday(datetime(2026, 10, 17)), 7)  # 周六

    def test_breakfast_coupon_available_with_minutes_left(self):
        """09:07 时，10:29:59 失效的早餐券应可用，且剩余约 82 分钟。"""
        verdict = coupons.evaluate(BREAKFAST_COUPON, datetime(2026, 10, 11, 9, 7, 38), 1)
        self.assertIs(verdict.status, coupons.CouponStatus.AVAILABLE)
        self.assertEqual(verdict.remaining_minutes, 82)

    def test_breakfast_coupon_expired_after_window(self):
        """同一个券，过了 10:29:59 就作废——不是「还有几天」。"""
        verdict = coupons.evaluate(BREAKFAST_COUPON, datetime(2026, 10, 11, 12, 0, 0), 1)
        self.assertIs(verdict.status, coupons.CouponStatus.EXPIRED)

    def test_boundary_exactly_at_window_start(self):
        verdict = coupons.evaluate(BREAKFAST_COUPON, datetime(2026, 10, 11, 5, 0, 0), 1)
        self.assertIs(verdict.status, coupons.CouponStatus.AVAILABLE)

    def test_wrong_take_mode_detected(self):
        """早餐券只适用 beType 1/5，外送(2)不可用——提醒了也用不了。"""
        verdict = coupons.evaluate(BREAKFAST_COUPON, datetime(2026, 10, 11, 9, 0, 0), 2)
        self.assertIs(verdict.status, coupons.CouponStatus.WRONG_TAKE_MODE)

    def test_app_only_coupon_flagged(self):
        c = dict(BREAKFAST_COUPON, onlyAppCanRedeem=True)
        verdict = coupons.evaluate(c, datetime(2026, 10, 11, 9, 0, 0), 1)
        self.assertIs(verdict.status, coupons.CouponStatus.WRONG_CHANNEL)

    def test_used_up_coupon_flagged(self):
        c = dict(BREAKFAST_COUPON, totalAvailableCount=0)
        verdict = coupons.evaluate(c, datetime(2026, 10, 11, 9, 0, 0), 1)
        self.assertIs(verdict.status, coupons.CouponStatus.USED_UP)

    def test_future_coupon_not_started(self):
        verdict = coupons.evaluate(WEEKDAY_COUPON, datetime(2026, 10, 11, 12, 0, 0), 1)
        self.assertIs(verdict.status, coupons.CouponStatus.NOT_STARTED)
        self.assertIsNotNone(verdict.starts_in_minutes)

    def test_multiday_coupon_weekday_filter(self):
        """周一至周五的券，周六使用应被判定为过期（已超出整体范围）。"""
        monday_noon = datetime(2026, 10, 12, 12, 0, 0)
        self.assertIs(
            coupons.evaluate(WEEKDAY_COUPON, monday_noon, 1).status,
            coupons.CouponStatus.AVAILABLE,
        )
        saturday = datetime(2026, 10, 17, 12, 0, 0)
        self.assertIs(
            coupons.evaluate(WEEKDAY_COUPON, saturday, 1).status,
            coupons.CouponStatus.EXPIRED,
        )

    def test_multiday_coupon_before_daily_start(self):
        """10-12 10:30 才开始，当天 08:00 应判定为未开始。"""
        verdict = coupons.evaluate(WEEKDAY_COUPON, datetime(2026, 10, 12, 8, 0, 0), 1)
        self.assertIs(verdict.status, coupons.CouponStatus.NOT_STARTED)

    def test_urgent_sorted_by_remaining_minutes(self):
        """即将失效的券要按紧迫度排序——这是提醒场景的核心输出。"""
        soon = dict(BREAKFAST_COUPON, title="半小时后失效",
                    tradeEndDateTime="2026-10-11 09:40:00")
        soon["tradeTimePeriods"] = [{"startTime": "05:00:00", "endTime": "09:40:00"}]
        later = dict(BREAKFAST_COUPON, title="一个多小时后失效")
        result = coupons.urgent([later, soon], datetime(2026, 10, 11, 9, 7, 0), 1, threshold_minutes=120)
        self.assertEqual([c["title"] for _, c, _ in result], ["半小时后失效", "一个多小时后失效"])

    def test_urgent_excludes_far_future_coupons(self):
        result = coupons.urgent([BREAKFAST_COUPON], datetime(2026, 10, 11, 5, 0, 0), 1,
                                threshold_minutes=90)
        self.assertEqual(result, [])


class TestGuard(unittest.TestCase):
    """闸门：默认必须拦住，拦不住就是真实扣费事故。"""

    def test_write_tool_blocked_without_confirmation(self):
        self.assertFalse(guard.check("create-order", {"storeCode": "1"}).ok)
        self.assertFalse(guard.check("cancel-order", {"orderId": "x"}).ok)
        self.assertFalse(guard.check("draw-lottery").ok)

    def test_confirmed_write_passes(self):
        result = guard.check("create-order", {"storeCode": "1"}, confirmed=True)
        self.assertTrue(result.ok)

    def test_unknown_tool_blocked_and_explains(self):
        result = guard.check("some-new-tool", confirmed=True)
        self.assertFalse(result.ok)
        self.assertIn("未知的写操作", result.reason)

    def test_amount_limit_requires_second_confirmation(self):
        result = guard.check("create-order", {}, confirmed=True, amount=350.0, amount_limit=200.0)
        self.assertFalse(result.ok)
        self.assertIn("上限", result.reason)

    def test_missing_required_field_blocked(self):
        result = guard.check(
            "create-order", {}, confirmed=True, required_fields=("storeCode",)
        )
        self.assertFalse(result.ok)
        self.assertIn("storeCode", result.reason)

    def test_lottery_requires_preflight(self):
        blocked = guard.check("draw-lottery", confirmed=True)
        self.assertFalse(blocked.ok)
        passed = guard.check("draw-lottery", confirmed=True, preflight_done=True)
        self.assertTrue(passed.ok)

    def test_require_raises_with_reason(self):
        with self.assertRaises(guard.GuardError) as ctx:
            guard.require("create-order", {})
        self.assertIn("确认", str(ctx.exception))

    def test_receipt_notice_contains_revocation(self):
        """设计标准 P0：回执必须写明可撤销与时限。"""
        notice = guard.receipt_notice(38.5)
        self.assertIn("取消", notice)
        self.assertIn("10 分钟", notice)
        self.assertIn("38.50", notice)


if __name__ == "__main__":
    unittest.main(verbosity=2)
