"""个性化：持久化用户画像 + 确定性应用规则。

设计原则（对齐仓库 Rule 5：确定性逻辑禁止交给模型）：
- 画像数据由 Skill 自有文件持久化（MCP 不存自由文本的用户备注）
- 偏好提取（从对话中识别"喜欢/不喜欢/忌口"）由模型做（分类任务）
- 筛选 / 高亮 / 匹配由本模块的代码做（确定性）

输入三类：
1. 持久画像 profile（likes / dislikes / dietary …）
2. 时间日期（MCP now-time-info 或本地时间）
3. 当前会话上下文（本次 query）

输出：
- 推荐时过滤掉用户不喜欢的（如不吃辣 → 不推辣的）
- 高亮用户喜欢的（如喜欢冰淇淋 → 新品/券命中时主动告知）
- 基于时间给出餐段提示（早餐 / 午市 / 晚市）
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Iterable

DEFAULT_PROFILE_PATH = os.path.join(os.path.dirname(__file__), "user_profile.json")

# 受控关键词表：把"用户说的偏好词"映射到"餐品名/标签里要匹配的关键词"。
# 不在表里的偏好词，直接用原词做子串匹配（兜底）。
DISLIKE_KEYWORDS = {
    "辣": ["辣", "麻辣", "香辣", "酸辣", "川", "椒", "藤椒", "爆辣"],
    "香菜": ["香菜", "芫荽"],
    "葱": ["葱"],
    "海鲜": ["虾", "蟹", "鱼", "海鲜", "鱿鱼"],
    "猪肉": ["猪", "培根", "火腿"],
}
LIKE_KEYWORDS = {
    "冰淇淋": ["冰淇淋", "麦旋风", "圆筒", "圣代", "甜筒", "冰激凌"],
    "咖啡": ["咖啡", "美式", "拿铁", "卡布", "麦咖啡", "McCafé"],
    "薯条": ["薯条", "薯格", "薯饼"],
    "鸡": ["鸡", "麦辣", "鸡块", "鸡翅"],
}


def load_profile(path: str = DEFAULT_PROFILE_PATH) -> dict:
    """读取画像；文件不存在 / 为空 / 损坏时返回空画像（不创建文件、不崩溃）。"""
    if not os.path.exists(path):
        return {"likes": [], "dislikes": [], "dietary": [], "updated_at": None}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, ValueError):
        # 空文件或坏 JSON：宁可回退到空画像，也不要让 Skill 崩溃
        return {"likes": [], "dislikes": [], "dietary": [], "updated_at": None}
    if not isinstance(data, dict):
        return {"likes": [], "dislikes": [], "dietary": [], "updated_at": None}
    data.setdefault("likes", [])
    data.setdefault("dislikes", [])
    data.setdefault("dietary", [])
    return data


def save_profile(profile: dict, path: str = DEFAULT_PROFILE_PATH) -> None:
    """写回画像，并打时间戳。"""
    profile["updated_at"] = datetime.now().isoformat(timespec="seconds")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)


def merge_preference(profile: dict, category: str, value: str) -> bool:
    """把一条偏好写入画像（去重）。category ∈ {likes, dislikes, dietary}。
    返回是否发生了变化（用于决定要不要落盘 + 要不要回应用户"已记住"）。"""
    if category not in ("likes", "dislikes", "dietary"):
        raise ValueError(f"未知画像类别: {category}")
    lst = profile.setdefault(category, [])
    value = value.strip()
    if value and value not in lst:
        lst.append(value)
        return True
    return False


def _keywords_for(category_map: dict, value: str) -> list[str]:
    return category_map.get(value, [value])


def _hit(text: str, keywords: Iterable[str]) -> bool:
    return any(k and k in text for k in keywords)


def is_disliked(item_name: str, profile: dict) -> bool:
    """餐品名是否命中用户的'不喜欢'。"""
    for d in profile.get("dislikes", []):
        if _hit(item_name, _keywords_for(DISLIKE_KEYWORDS, d)):
            return True
    return False


def matches_like(item_name: str, profile: dict) -> list[str]:
    """餐品名命中了用户哪些'喜欢'，返回命中的偏好词列表。"""
    hits = []
    for like in profile.get("likes", []):
        if _hit(item_name, _keywords_for(LIKE_KEYWORDS, like)):
            hits.append(like)
    return hits


def filter_recommendations(
    items: list[dict], profile: dict
) -> tuple[list[dict], list[tuple[dict, str]]]:
    """items: 含 'name' 的餐品列表。
    返回 (保留项, 被移除项及原因)。被移除的也会告知用户"为什么没推"。"""
    kept, removed = [], []
    for it in items:
        name = it.get("name", "")
        if is_disliked(name, profile):
            removed.append((it, "按偏好避开"))
        else:
            kept.append(it)
    return kept, removed


def time_context(now: datetime | None = None) -> dict:
    """基于当前时间给出餐段与周末标记，供推荐时做时间个性化。"""
    now = now or datetime.now()
    h, m = now.hour, now.minute
    if h < 10 or (h == 10 and m < 30):
        window = "早餐"
    elif 11 <= h <= 14:
        window = "午市"
    elif 17 <= h <= 21:
        window = "晚市"
    else:
        window = "其他"
    return {
        "hour": h,
        "minute": m,
        "weekday": now.weekday(),  # 0 = 周一
        "is_weekend": now.weekday() >= 5,
        "meal_window": window,
    }


def liked_relevant(
    items: list[dict], profile: dict
) -> list[tuple[dict, list[str]]]:
    """从一批餐品/券中，挑出命中用户'喜欢'的，用于主动告知（非推销，只因你说过喜欢）。
    items 元素需含 'name' 或 'title'。"""
    out = []
    for it in items:
        name = it.get("name", "") or it.get("title", "")
        hits = matches_like(name, profile)
        if hits:
            out.append((it, hits))
    return out
