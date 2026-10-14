#!/usr/bin/env python3
"""个性化模块单测。

只测确定性逻辑：画像读写/去重、偏好筛选、关键词匹配、时间餐段、喜欢命中。
这些错了会直接损害用户体验（推了用户讨厌的、漏了用户喜欢的），必须有断言。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import personalize  # noqa: E402


class TestProfile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, mode="w", encoding="utf-8"
        )
        self.tmp.close()
        self.path = self.tmp.name

    def tearDown(self):
        Path(self.path).unlink(missing_ok=True)

    def test_load_missing_returns_empty(self):
        p = personalize.load_profile(self.path)
        self.assertEqual(p["likes"], [])
        self.assertEqual(p["dislikes"], [])

    def test_merge_dedup_and_timestamp(self):
        p = personalize.load_profile(self.path)
        self.assertTrue(personalize.merge_preference(p, "likes", "冰淇淋"))
        self.assertFalse(personalize.merge_preference(p, "likes", "冰淇淋"))  # 重复
        self.assertTrue(personalize.merge_preference(p, "dislikes", "辣"))
        personalize.save_profile(p, self.path)
        reloaded = personalize.load_profile(self.path)
        self.assertEqual(reloaded["likes"], ["冰淇淋"])
        self.assertEqual(reloaded["dislikes"], ["辣"])
        self.assertIsNotNone(reloaded["updated_at"])

    def test_merge_unknown_category_raises(self):
        p = personalize.load_profile(self.path)
        with self.assertRaises(ValueError):
            personalize.merge_preference(p, "hobby", "篮球")


class TestMatching(unittest.TestCase):
    def setUp(self):
        self.profile = {"likes": ["冰淇淋"], "dislikes": ["辣"], "dietary": []}

    def test_is_disliked_sichuan_spicy(self):
        # "辣" 映射到多个关键词，麦辣鸡腿堡含"辣"
        self.assertTrue(personalize.is_disliked("麦辣鸡腿堡", self.profile))
        # 不辣的不会被误伤
        self.assertFalse(personalize.is_disliked("麦香鸡", self.profile))

    def test_is_disliked_unmapped_word(self):
        p = {"likes": [], "dislikes": ["香菜"], "dietary": []}
        self.assertTrue(personalize.is_disliked("香菜鸡腿堡", p))

    def test_matches_like_icecream(self):
        hits = personalize.matches_like("蓝莓爆爆珠麦旋风", self.profile)
        self.assertEqual(hits, ["冰淇淋"])
        self.assertEqual(personalize.matches_like("薯条", self.profile), [])


class TestFilter(unittest.TestCase):
    def setUp(self):
        self.profile = {"likes": ["冰淇淋"], "dislikes": ["辣"], "dietary": []}
        self.items = [
            {"name": "麦辣鸡腿堡"},
            {"name": "蓝莓爆爆珠麦旋风"},
            {"name": "薯条"},
        ]

    def test_filter_removes_disliked(self):
        kept, removed = personalize.filter_recommendations(self.items, self.profile)
        self.assertEqual([i["name"] for i in kept], ["蓝莓爆爆珠麦旋风", "薯条"])
        self.assertEqual(removed[0][0]["name"], "麦辣鸡腿堡")
        self.assertEqual(removed[0][1], "按偏好避开")


class TestTimeContext(unittest.TestCase):
    def test_breakfast_window(self):
        tc = personalize.time_context(datetime(2026, 10, 12, 9, 0))
        self.assertEqual(tc["meal_window"], "早餐")
        self.assertFalse(tc["is_weekend"])

    def test_lunch_window_weekend(self):
        # 2026-10-10 是周六
        tc = personalize.time_context(datetime(2026, 10, 10, 12, 30))
        self.assertEqual(tc["meal_window"], "午市")
        self.assertTrue(tc["is_weekend"])

    def test_dinner_window(self):
        tc = personalize.time_context(datetime(2026, 10, 12, 19, 0))
        self.assertEqual(tc["meal_window"], "晚市")


class TestLikedRelevant(unittest.TestCase):
    def test_picks_icecream_from_items(self):
        profile = {"likes": ["冰淇淋"], "dislikes": [], "dietary": []}
        items = [
            {"name": "灰焰圆筒"},
            {"title": "9.9元超值早餐正式上新"},
            {"name": "麦辣鸡腿堡"},
        ]
        out = personalize.liked_relevant(items, profile)
        # 灰焰圆筒命中"冰淇淋"
        names = [ (it.get("name") or it.get("title")) for it, _ in out ]
        self.assertIn("灰焰圆筒", names)
        self.assertNotIn("麦辣鸡腿堡", names)


if __name__ == "__main__":
    unittest.main(verbosity=2)
