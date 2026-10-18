#!/usr/bin/env python3
"""fast_order.py 单测：选方式 / 选店 / 预约窗口 / ETA。

门店选店用真实抓取数据（05_stores_pickup.json）；预约窗口用受控时间。
"""
from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # 让 fixtures 可导入

import fast_order  # noqa: E402
from fixtures import probe_dir  # noqa: E402

PROBE = probe_dir() / "05_stores_pickup.json"


def load_stores() -> list[dict]:
    with open(PROBE, encoding="utf-8") as f:
        return json.load(f)["data"]  # 顶层 data 即门店列表


WINDOW_TEXT = "早餐(06:14至10:15)，午餐(10:44至14:15)，下午茶(14:44至16:45)，夜市(17:14至21:45)，宵夜(22:14至22:46)"
OPTS = [{"date": "2026-10-11", "today": True, "reservationOptionText": WINDOW_TEXT}]


class TestMethod(unittest.TestCase):
    def test_user_specified_wins(self):
        self.assertEqual(fast_order.choose_pickup_method({"user_specified": "dt"}), "dt")

    def test_driving(self):
        self.assertEqual(fast_order.choose_pickup_method({"is_driving": True}), "dt")

    def test_address(self):
        self.assertEqual(fast_order.choose_pickup_method({"has_address": True}), "delivery")

    def test_default(self):
        self.assertEqual(fast_order.choose_pickup_method({}), "pickup")


class TestStore(unittest.TestCase):
    def setUp(self):
        self.stores = load_stores()

    def test_picks_min_distance_open(self):
        plan_in = [s for s in self.stores if s.get("businessStatus") and s.get("reservation")]
        min_dist = min(float(s["distance"]) for s in plan_in)
        got = fast_order.select_nearest_store(self.stores)
        self.assertEqual(float(got["distance"]), min_dist)

    def test_no_open_store(self):
        closed = [{**s, "businessStatus": False} for s in self.stores]
        self.assertIsNone(fast_order.select_nearest_store(closed))

    def test_prefer_dt(self):
        # prefer_dt 时若有无"得来速"店则只在其中选
        got = fast_order.select_nearest_store(self.stores, prefer_dt=True)
        self.assertIn("得来速", got["storeName"])


class TestWindows(unittest.TestCase):
    def test_parse(self):
        w = fast_order.parse_windows(WINDOW_TEXT)
        self.assertEqual(len(w), 5)
        self.assertEqual(w[0], ("早餐", "06:14", "10:15"))
        self.assertEqual(w[-1], ("宵夜", "22:14", "22:46"))

    def test_within_window(self):
        # 09:00 + eta0 + buf5 = 09:05 → 早餐
        resv = fast_order.pick_reservation_window(datetime(2026, 10, 11, 9, 0), 0, OPTS)
        self.assertEqual(resv["window"], "早餐")
        self.assertFalse(resv["adjusted"])
        self.assertTrue(resv["reservationDate"].startswith("2026-10-11 09:05"))

    def test_gap_adjusted(self):
        # 10:30 + 0 + 5 = 10:35 → 介于早餐结束(10:15)与午餐开始(10:44) → 顺延到 10:44
        resv = fast_order.pick_reservation_window(datetime(2026, 10, 11, 10, 30), 0, OPTS)
        self.assertEqual(resv["window"], "午餐")
        self.assertTrue(resv["adjusted"])
        self.assertEqual(resv["reservationDate"], "2026-10-11 10:44")

    def test_after_all_windows(self):
        # 23:00 + 0 + 5 = 23:05 → 晚于宵夜(22:46) → 当天无法预约
        self.assertIsNone(fast_order.pick_reservation_window(datetime(2026, 10, 11, 23, 0), 0, OPTS))

    def test_no_options(self):
        self.assertIsNone(fast_order.pick_reservation_window(datetime(2026, 10, 11, 9, 0), 0, []))


class TestEta(unittest.TestCase):
    def test_walking(self):
        self.assertEqual(fast_order.estimate_eta_minutes(605, "walking"), 9)  # 605/70≈8.6→9

    def test_driving(self):
        self.assertEqual(fast_order.estimate_eta_minutes(10616, "driving"), 42)  # 10616/250=42.46→42

    def test_min_one(self):
        self.assertEqual(fast_order.estimate_eta_minutes(10, "walking"), 1)


class TestPlan(unittest.TestCase):
    def test_real_plan(self):
        plan = fast_order.build_fast_order_plan(load_stores(), {}, datetime(2026, 10, 11, 9, 0))
        self.assertNotIn("error", plan)
        self.assertEqual(plan["method"], "pickup")
        self.assertGreaterEqual(plan["eta_minutes"], 1)
        self.assertIsNotNone(plan["reservation"])
        self.assertIn(plan["reservation"]["window"], ["早餐", "午餐", "下午茶", "夜市", "宵夜"])
        self.assertIn("storeCode", plan["store"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
