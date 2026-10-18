#!/usr/bin/env python3
"""reminders.py 单测：积分目标 + 券紧迫度，用真实抓取数据验证。"""
from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # 让 fixtures 可导入

import reminders  # noqa: E402
from fixtures import probe_dir  # noqa: E402

PROBE = probe_dir()


def load(name: str) -> dict:
    with open(PROBE / name, encoding="utf-8") as f:
        return json.load(f)


class TestPoints(unittest.TestCase):
    def setUp(self):
        self.account = load("02_my_account.json")
        self.mall = load("11_mall_products.json")

    def test_real_balance_picks_affordable(self):
        # 真实可用积分 180.7，能换的最值钱项是板烧两件套(100分)
        out = reminders.pick_points_target(self.account, self.mall)
        self.assertEqual(out["balance"], 180.7)
        self.assertIsNotNone(out["near"])
        self.assertTrue(out["near"]["reachable"])
        self.assertIn("板烧鸡腿堡两件套", out["near"]["name"])

    def test_low_balance_shows_gap(self):
        acct = {"data": {"availablePoint": "30"}}
        out = reminders.pick_points_target(acct, self.mall)
        self.assertFalse(out["near"]["reachable"])
        self.assertEqual(out["near"]["gap"], 20)  # 最近可换香芋派 50 分，差 20
        self.assertIn("香芋派", out["near"]["name"])

    def test_empty_account(self):
        self.assertIsNone(reminders.pick_points_target({}, self.mall))
        self.assertEqual(
            reminders.pick_points_target({"data": {"availablePoint": "0"}}, self.mall)["near"], None
        )


class TestCoupon(unittest.TestCase):
    def setUp(self):
        self.coupons = load("03_my_coupons.json")

    def test_urgent_picked_in_morning(self):
        # 09:07 时早餐券剩 82 分钟，阈值 120 → 应被选中
        out = reminders.build_coupon_reminder(self.coupons, datetime(2026, 10, 11, 9, 7, 38), 1, 120)
        self.assertTrue(any("厚松饼猪柳蛋" in c["title"] for c in out))
        self.assertTrue(all("剩" in c["urgency"] for c in out))

    def test_none_urgent_at_night(self):
        # 21:53 时该券已过窗口，紧迫项应为空
        out = reminders.build_coupon_reminder(self.coupons, datetime(2026, 10, 11, 21, 53, 0), 1, 120)
        self.assertEqual(out, [])

    def test_delivery_mode_filters(self):
        # beType=2（外送）下多数券不可用 → 紧迫项更少或为空
        out = reminders.build_coupon_reminder(self.coupons, datetime(2026, 10, 11, 9, 7, 38), 2, 120)
        self.assertIsInstance(out, list)

    def test_interest_match_marks_liked(self):
        # 真实数据：09:07 紧迫券含「厚松饼猪柳蛋套餐」；用户喜欢猪柳蛋 → 标 liked
        profile = {"likes": ["猪柳蛋"], "dislikes": [], "dietary": []}
        out = reminders.build_coupon_reminder(
            self.coupons, datetime(2026, 10, 11, 9, 7, 38), 1, 120, profile=profile
        )
        liked = [c for c in out if c.get("liked")]
        self.assertTrue(any("厚松饼猪柳蛋" in c["title"] for c in liked))

    def test_no_profile_no_liked(self):
        # 不传 profile 时不打 liked，行为与旧版一致
        out = reminders.build_coupon_reminder(self.coupons, datetime(2026, 10, 11, 9, 7, 38), 1, 120)
        self.assertTrue(all(not c.get("liked") for c in out))

    def test_crafted_match(self):
        # 构造数据：用户喜欢薯条 → "中薯条买一送一" 命中；券须形如 evaluate 可判定为 AVAILABLE 且即将失效
        payload = {
            "data": {
                "coupons": [
                    {
                        "title": "中薯条买一送一",
                        "beTypes": [1],
                        "tradeStartDateTime": "2026-10-11 00:00:00",
                        "tradeEndDateTime": "2026-10-11 09:50:00",
                    },
                    {
                        "title": " unrelated 券",
                        "beTypes": [1],
                        "tradeStartDateTime": "2026-10-11 00:00:00",
                        "tradeEndDateTime": "2026-10-12 09:50:00",  # 远未失效 → 非紧迫
                    },
                ]
            }
        }
        profile = {"likes": ["薯条"], "dislikes": [], "dietary": []}
        out = reminders.build_coupon_reminder(payload, datetime(2026, 10, 11, 9, 0), 1, 120, profile=profile)
        matched = [c for c in out if c.get("liked")]
        self.assertEqual([c["title"] for c in matched], ["中薯条买一送一"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
