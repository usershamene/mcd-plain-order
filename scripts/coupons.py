#!/usr/bin/env python3
"""优惠券可用性判定。

核心事实（实测）：麦当劳券大多是【日内时段券】——
例如早餐券只在当天 05:00-10:29 有效，过期即作废。
因此核心指标是「距今日窗口关闭还剩多少分钟」，而不是「距到期还有几天」。

字段语义说明：
- weekdays：实测反推为 1=周日、2=周一 … 7=周六（12 个数据点验证）。
  官方文档未说明，此为本项目实测结论，若与官方口径冲突以实测为准。
- tradeTimePeriods：日内可用窗口；为空或 isAllTradeTime=1 时视为全天可用。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from enum import Enum

DATETIME_FMT = "%Y-%m-%d %H:%M:%S"


class CouponStatus(str, Enum):
    AVAILABLE = "available"  # 现在可用
    NOT_STARTED = "not_started"  # 整体尚未开始
    EXPIRED = "expired"  # 整体已过期（单日券含"今日窗口已过"）
    OUTSIDE_TODAY_WINDOW = "outside_today_window"  # 券仍有效，但今天这个时段不行
    NOT_IN_WEEKDAY = "not_in_weekday"  # 今天不在适用星期
    WRONG_CHANNEL = "wrong_channel"  # 仅限 APP 或仅限 POS 核销
    WRONG_TAKE_MODE = "wrong_take_mode"  # 不适用于当前取餐方式
    USED_UP = "used_up"  # 可用次数已用完


@dataclass(frozen=True)
class CouponVerdict:
    status: CouponStatus
    reason: str
    remaining_minutes: int | None = None  # 仅 AVAILABLE 时给出
    starts_in_minutes: int | None = None  # 仅 NOT_STARTED 时给出


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, DATETIME_FMT)
    except (TypeError, ValueError):
        return None


def to_mcd_weekday(moment: datetime) -> int:
    """转成券接口的星期编号：1=周日、2=周一 … 7=周六。"""
    return (moment.isoweekday() % 7) + 1


def _parse_time(value: str) -> time | None:
    try:
        return datetime.strptime(value, "%H:%M:%S").time()
    except (TypeError, ValueError):
        return None


def evaluate(
    coupon: dict,
    now: datetime,
    be_type: int,
    *,
    alert_threshold_minutes: int | None = None,
) -> CouponVerdict:
    """判定单张券在当前时刻、当前取餐方式下是否可用。

    判定顺序（先排除、后计算）：
      1 渠道限制 → 2 取餐方式 → 3 整体时间范围 → 4 星期与日内窗口 → 5 剩余次数
    """
    # 1 渠道限制：提醒了也用不了，必须最先排除
    if coupon.get("onlyAppCanRedeem"):
        return CouponVerdict(CouponStatus.WRONG_CHANNEL, "该券只能在麦当劳 App 内使用")
    if coupon.get("onlyPosCanRedeem"):
        return CouponVerdict(CouponStatus.WRONG_CHANNEL, "该券只能在门店收银机使用")

    # 2 取餐方式
    be_types = coupon.get("beTypes") or []
    if be_types and be_type not in be_types:
        return CouponVerdict(
            CouponStatus.WRONG_TAKE_MODE,
            f"该券不适用于当前取餐方式（适用 beType={be_types}）",
        )

    # 3 整体时间范围
    start = _parse(coupon.get("tradeStartDateTime"))
    end = _parse(coupon.get("tradeEndDateTime"))
    if start is None or end is None:
        return CouponVerdict(CouponStatus.EXPIRED, "券缺少有效时间信息，无法判定")
    if now > end:
        return CouponVerdict(CouponStatus.EXPIRED, "该券已过期")
    if now < start:
        return CouponVerdict(
            CouponStatus.NOT_STARTED,
            "该券尚未开始生效",
            starts_in_minutes=max(0, int((start - now).total_seconds() // 60)),
        )

    # 4 星期与日内窗口
    weekdays = coupon.get("weekdays") or []
    if weekdays and to_mcd_weekday(now) not in weekdays:
        return CouponVerdict(CouponStatus.NOT_IN_WEEKDAY, "今天不是该券的适用星期")

    deadline = end
    periods = coupon.get("tradeTimePeriods") or []
    if periods and not coupon.get("isAllTradeTime"):
        inside = False
        for period in periods:
            p_start = _parse_time(period.get("startTime", ""))
            p_end = _parse_time(period.get("endTime", ""))
            if p_start is None or p_end is None:
                continue
            if p_start <= now.time() <= p_end:
                inside = True
                period_deadline = now.replace(
                    hour=p_end.hour, minute=p_end.minute, second=p_end.second
                )
                if period_deadline < deadline:
                    deadline = period_deadline
                break
        if not inside:
            return CouponVerdict(
                CouponStatus.OUTSIDE_TODAY_WINDOW,
                "现在不在该券今天的可用时段内",
            )

    # 5 剩余次数
    remaining_count = coupon.get("totalAvailableCount")
    if isinstance(remaining_count, int) and remaining_count <= 0:
        return CouponVerdict(CouponStatus.USED_UP, "该券的可用次数已用完")

    remaining_minutes = max(0, int((deadline - now).total_seconds() // 60))
    reason = f"现在可用，距失效还有 {remaining_minutes} 分钟"
    if alert_threshold_minutes is not None and remaining_minutes <= alert_threshold_minutes:
        reason = f"现在可用，且只剩 {remaining_minutes} 分钟就要失效"
    return CouponVerdict(CouponStatus.AVAILABLE, reason, remaining_minutes=remaining_minutes)


def urgent(coupons: list[dict], now: datetime, be_type: int, *, threshold_minutes: int = 90):
    """筛出「现在可用且即将失效」的券，按剩余分钟升序。

    这是「权益提醒」场景的核心入口——注意阈值是分钟级，不是天级。
    """
    result: list[tuple[int, dict, CouponVerdict]] = []
    for coupon in coupons:
        verdict = evaluate(coupon, now, be_type)
        if verdict.status is CouponStatus.AVAILABLE and verdict.remaining_minutes is not None:
            if verdict.remaining_minutes <= threshold_minutes:
                result.append((verdict.remaining_minutes, coupon, verdict))
    result.sort(key=lambda item: item[0])
    return result
