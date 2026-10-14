"""新品发现：从菜单中筛出「上新」类餐品，并接个性化层标记用户喜欢的。

输入来源：MCP query-meals 的原始返回（data.categories + data.meals）。
确定性逻辑（Rule 5）：哪些算「新品」由 tags 关键字决定，不交模型。

与个性化的关系：提取出的新品，再交给 personalize.matches_like 标记是否命中
用户说过喜欢的品类（如冰淇淋）——命中则在输出里标 liked=True，渲染层据此
显示「你喜欢的」，实现「喜欢冰淇淋 → 主动告知冰淇淋新品」。
"""
from __future__ import annotations

import personalize

# 含这些子串的 tag 视为新品。不用裸「新」以避免「新鲜/新鲜食材」误判。
NEW_TAG_KEYWORDS = ["上新", "清爽上新"]


def _is_new_tag(tag: str) -> bool:
    return any(k in tag for k in NEW_TAG_KEYWORDS)


def extract_new_items(meals_payload: dict) -> list[dict]:
    """从 query-meals 返回里提取带「上新」类 tag 的餐品。

    返回列表，每项：{code, name, price, original_price, tag}。
    按价格升序（便宜的新品先看，信息密度优先）；同一 code 去重。
    """
    data = meals_payload.get("data", meals_payload)
    detail_map = data.get("meals", {})
    cats = data.get("categories", [])
    out, seen = [], set()
    for cat in cats:
        for it in cat.get("meals", []):
            tags = it.get("tags", [])
            new_tags = [t for t in tags if _is_new_tag(t)]
            if not new_tags:
                continue
            code = it.get("code")
            if code in seen:
                continue
            seen.add(code)
            detail = detail_map.get(code, {})
            price = detail.get("currentPrice") or detail.get("originalPrice") or ""
            orig = detail.get("originalPrice") or ""
            out.append(
                {
                    "code": code,
                    "name": detail.get("name") or it.get("name") or "",
                    "price": price,
                    "original_price": orig,
                    "tag": new_tags[0],
                }
            )
    out.sort(key=lambda x: _to_float(x.get("price")))
    return out


def _to_float(v) -> float:
    try:
        return float(str(v).replace("¥", "").strip())
    except (ValueError, TypeError):
        return float("inf")


def new_items_for_user(meals_payload: dict, profile: dict) -> list[dict]:
    """提取新品，并标记是否命中用户「喜欢」（个性化层）。
    返回项追加 liked: bool。"""
    items = extract_new_items(meals_payload)
    out = []
    for it in items:
        hits = personalize.matches_like(it["name"], profile)
        it2 = dict(it)
        it2["liked"] = bool(hits)
        out.append(it2)
    return out
