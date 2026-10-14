#!/usr/bin/env python3
"""场景路由：把用户意图映射到 beType / orderType / beCode 规则。

设计约束（来自实测，见 references/be_type_matrix.md）：
- 到店自取：beCode 必须【不传】，传了会报错
- 得来速 / 麦乐送 / 团餐：beCode 必传，且来源不同
- 配对错误 → 业务错误 60002

本模块是纯查表 + 校验，不含任何模型判断。所有参数构造都必须经过 build_args，
以确保不会出现「到店场景误传 beCode」这类错误。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TakeMode(str, Enum):
    """取餐方式。"""

    PICKUP = "pickup"  # 到店自取
    DT = "dt"  # 得来速（Drive Through）
    DELIVERY = "delivery"  # 麦乐送
    GROUP = "group"  # 企业团餐


class RouteError(Exception):
    """路由参数错误（会在本地拦截，不消耗 MCP 调用）。"""


@dataclass(frozen=True)
class RoutePlan:
    take_mode: TakeMode
    be_type: int
    order_type: int
    needs_be_code: bool
    be_code_source: str | None
    extra_required: tuple[str, ...]


# 唯一的真值来源。任何新增取餐方式都必须在这里登记。
_ROUTES: dict[TakeMode, RoutePlan] = {
    TakeMode.PICKUP: RoutePlan(TakeMode.PICKUP, 1, 1, False, None, ()),
    TakeMode.DT: RoutePlan(TakeMode.DT, 5, 1, True, "query-nearby-stores", ()),
    TakeMode.DELIVERY: RoutePlan(
        TakeMode.DELIVERY, 2, 2, True, "delivery-query-stores", ("addressId",)
    ),
    TakeMode.GROUP: RoutePlan(
        TakeMode.GROUP,
        6,
        2,
        True,
        "delivery-query-stores",
        ("addressId", "gmServiceCode"),
    ),
}

# 得来速需要用户明确表示开车前往，不能由系统替他决定，故不参与自动选择。
_AUTO_CHOOSABLE = (TakeMode.PICKUP, TakeMode.DELIVERY)


def resolve(take_mode: TakeMode) -> RoutePlan:
    """取餐方式 → 路由计划。"""
    try:
        return _ROUTES[TakeMode(take_mode)]
    except (KeyError, ValueError) as exc:
        raise RouteError(f"未知取餐方式：{take_mode!r}") from exc


def choose_take_mode(*, has_address: bool, prefer: TakeMode | None = None) -> TakeMode:
    """自动判断取餐方式。

    - 用户明确指定 → 用用户的
    - 否则：有配送地址 → 麦乐送；没有 → 到店自取
    - 得来速必须用户显式指定（涉及"是否开车"这一系统无法推断的前提）
    """
    if prefer is not None:
        return TakeMode(prefer)
    return TakeMode.DELIVERY if has_address else TakeMode.PICKUP


def build_args(
    plan: RoutePlan,
    *,
    store_code: str,
    items: list[dict] | None = None,
    be_code: str | None = None,
    address_id: str | None = None,
    gm_service_code: str | None = None,
    reservation_date: str | None = None,
    take_way_code: str | None = None,
    need_tableware: bool | None = None,
    remark: str | None = None,
    require_required: bool = True,
) -> dict:
    """构造工具参数。集中在这里，保证「到店不传 beCode」等规则不会写漏。

    传入与当前取餐方式无关的参数会直接报错，而不是静默忽略——
    静默忽略会让用户以为参数生效了。
    """
    args: dict = {
        "storeCode": store_code,
        "beType": plan.be_type,
        "orderType": plan.order_type,
    }

    if plan.needs_be_code:
        if not be_code:
            raise RouteError(
                f"{plan.take_mode.value} 场景必须提供 beCode（来源：{plan.be_code_source}）"
            )
        args["beCode"] = be_code
    elif be_code:
        raise RouteError(
            f"{plan.take_mode.value} 场景不允许传 beCode（官方明确该参数会报错）"
        )

    if items is not None:
        # MCP 的选购列表字段名是 productCode（不是 code）+ quantity（不是 qty）。
        # 在边界处统一转换，skill 内部仍可用更易读的 code/qty。
        normalized = []
        for it in items:
            nc = it.get("productCode") or it.get("code")
            nq = it.get("quantity", it.get("qty", 1))
            normalized.append({"productCode": nc, "quantity": nq})
        args["items"] = normalized

    for name, value in (
        ("addressId", address_id),
        ("gmServiceCode", gm_service_code),
        ("reservationDate", reservation_date),
        ("takeWayCode", take_way_code),
        ("remark", remark),
    ):
        if value is None:
            continue
        if name not in plan.extra_required and not _is_allowed_extra(plan, name):
            raise RouteError(f"{plan.take_mode.value} 场景不支持参数 {name}")
        args[name] = value

    if need_tableware is not None:
        args["needTableware"] = need_tableware

    validate(plan, args, require_required=require_required)
    return args


def _is_allowed_extra(plan: RoutePlan, name: str) -> bool:
    """除必填项外，各取餐方式允许的可选参数。"""
    optional = {
        TakeMode.PICKUP: {"takeWayCode", "reservationDate"},
        TakeMode.DT: {"takeWayCode", "reservationDate"},
        TakeMode.DELIVERY: {"remark", "reservationDate"},
        TakeMode.GROUP: {"reservationDate"},
    }
    return name in optional[plan.take_mode]


def validate(plan: RoutePlan, args: dict, *, require_required: bool = True) -> None:
    """校验参数完整性。宁可本地拦截，也不要浪费一次调用换回 60002。

    require_required=False 用于只读的 calculate-price 调用：它不需要 addressId 这类
    仅下单阶段才必填的字段，但仍接受并在下单阶段由 guard 强制。
    """
    if require_required:
        for field in plan.extra_required:
            if not args.get(field):
                raise RouteError(f"{plan.take_mode.value} 场景缺少必填参数 {field}")

    if not plan.needs_be_code and args.get("beCode"):
        raise RouteError(f"{plan.take_mode.value} 场景不允许传 beCode")

    reservation_date = args.get("reservationDate")
    if reservation_date is not None and not _is_valid_reservation(reservation_date):
        raise RouteError(
            f"reservationDate 格式应为 yyyy-MM-dd HH:mm，收到 {reservation_date!r}"
        )


def _is_valid_reservation(value: str) -> bool:
    from datetime import datetime

    try:
        datetime.strptime(value, "%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return False
    return True
