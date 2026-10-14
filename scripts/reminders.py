"""积分与券提醒编排：把账户 / 商城 / 券数据转成 render 的 reminders 段。

纯变换（Rule 5）：数据获取由 mcd_client 完成，本模块只做确定性计算，不碰网络。
- 积分：可用积分 + 最近的兑换目标（可换 / 还差多少）
- 券：即将过期的（复用 coupons.urgent）
"""
from __future__ import annotations

import coupons
import personalize


def _to_float(v) -> float:
    try:
        return float(str(v).replace("¥", "").strip())
    except (ValueError, TypeError):
        return 0.0


def pick_points_target(account: dict, mall_products) -> dict | None:
    """从账户 + 商城数据算积分提醒。

    返回 {"balance": 数值, "near": {"name", "gap", "reachable"}} 或 None（无数据）。
    规则：能兑换的里挑最值钱的（可换）；都不能换则挑差距最小的（还差）。
    point<10 的项（如 1 积分的促销占位）不参与，避免误选。
    """
    if not account:
        return None
    data = account.get("data", account)
    bal = _to_float(data.get("availablePoint", 0))
    if bal <= 0:
        return {"balance": bal, "near": None}

    items = mall_products.get("data", mall_products) if isinstance(mall_products, dict) else mall_products
    candidates = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        cost = _to_float(it.get("point"))
        if cost < 10:
            continue
        if it.get("status") not in (2, None):  # 仅取在售
            continue
        candidates.append((cost, it.get("spuName", "")))
    if not candidates:
        return {"balance": bal, "near": None}

    affordable = [(c, n) for c, n in candidates if c <= bal]
    if affordable:
        cost, name = max(affordable, key=lambda x: x[0])
        return {"balance": bal, "near": {"name": name, "gap": 0, "reachable": True}}
    cost, name = min(((c, n) for c, n in candidates if c > bal), key=lambda x: x[0])
    return {"balance": bal, "near": {"name": name, "gap": round(cost - bal, 1), "reachable": False}}


def build_coupon_reminder(
    coupons_payload: dict, now, be_type: int, threshold_minutes: int = 120, profile: dict | None = None
) -> list[dict]:
    """从券数据挑即将过期的，转成 render 的 🎫 段。复用 coupons.urgent。

    profile 非空时，对命中用户「喜欢」品类的券打 liked=True（兴趣匹配，非推销）：
    例如用户说过喜欢薯条，则「中薯条买一送一」会被标记，渲染层加「· 你喜欢」。
    """
    if not coupons_payload:
        return []
    cs = coupons_payload.get("data", coupons_payload).get("coupons", [])
    out = []
    for minutes, c, _status in coupons.urgent(cs, now, be_type, threshold_minutes=threshold_minutes):
        title = c.get("title", "")
        item = {"title": title, "urgency": f"剩 {minutes} 分钟"}
        if profile and personalize.matches_like(title, profile):
            item["liked"] = True
        out.append(item)
    return out
