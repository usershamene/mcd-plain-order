#!/usr/bin/env python3
"""newitems.py 单测：新品提取 + 个性化标记。

用真实抓取的菜单数据（07_meals_pickup.json）验证，不只 fixture。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # 让 fixtures 可导入

import newitems  # noqa: E402
from fixtures import probe_dir  # noqa: E402

PROBE = probe_dir() / "07_meals_pickup.json"


def load_probe() -> dict:
    with open(PROBE, encoding="utf-8") as f:
        return json.load(f)


class TestExtract(unittest.TestCase):
    def setUp(self):
        self.items = newitems.extract_new_items(load_probe())

    def test_finds_real_new_items(self):
        names = {it["name"] for it in self.items}
        self.assertEqual(len(names), 4)
        self.assertIn("蓝莓爆爆珠麦旋风", names)
        self.assertIn("灰焰圆筒", names)
        self.assertIn("泷愿成真麦旋风——大米风味", names)
        # 美汁源柠檬饮：名字含特殊引号，用子串判断避免字符歧义
        self.assertTrue(any("多汁柠柠" in n for n in names))

    def test_tag_assigned(self):
        for it in self.items:
            self.assertIn(it["tag"], ["上新", "清爽上新"])

    def test_sorted_by_price_asc(self):
        prices = [newitems._to_float(it["price"]) for it in self.items]
        self.assertEqual(prices, sorted(prices))
        # 最便宜的灰焰圆筒 ¥5 排第一
        self.assertEqual(self.items[0]["name"], "灰焰圆筒")

    def test_empty_payload(self):
        self.assertEqual(newitems.extract_new_items({}), [])


class TestPersonalized(unittest.TestCase):
    def test_icecream_lover_flagged(self):
        profile = {"likes": ["冰淇淋"], "dislikes": [], "dietary": []}
        items = newitems.new_items_for_user(load_probe(), profile)
        liked = {it["name"] for it in items if it["liked"]}
        # 三个麦旋风/圆筒类命中"冰淇淋"
        self.assertIn("蓝莓爆爆珠麦旋风", liked)
        self.assertIn("灰焰圆筒", liked)
        self.assertIn("泷愿成真麦旋风——大米风味", liked)
        # 柠檬饮不在冰淇淋关键词内
        self.assertFalse(any("多汁柠柠" in n for n in liked))

    def test_no_profile_no_flag(self):
        items = newitems.new_items_for_user(load_probe(), {})
        self.assertTrue(all(it["liked"] is False for it in items))


if __name__ == "__main__":
    unittest.main(verbosity=2)
