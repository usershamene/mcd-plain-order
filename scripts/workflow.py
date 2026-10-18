#!/usr/bin/env python3
"""极速点单端到端编排（确定性决策 + 网络委托）。

把「选店 → 算价 → 下单」串起来。**所有确定性逻辑**（路由 / 方案 / 金额映射 / 字段提取）
写死在代码里，不交模型；**网络调用**全部委托给注入的 client（线上用 MCDClient，测试用 FakeClient）。

没有有效 Token 时，用 tests 里的 FakeClient 回放真实探针 + 模拟计价/下单响应，
即可在本地确定性跑通整条链路，把真正的坑（beCode 配对 / takeWayCode 来源 / reservationDate
格式 / orderId 不生成）验证掉。

硬性约束（来自 be_type_matrix.md + tools-reference.md，实测）：
- beCode：到店不传；得来速/麦乐送来自对应 query-stores 响应；pairing 错 → 60002。
- takeWayCode：只能来自 calculate-price 的 takeWayList[].code，严禁自造；外送不传。
- reservationDate：仅到店/得来速预约场景传，格式 yyyy-MM-dd HH:mm。
- orderId：只来自 create-order 响应，禁止生成。
"""
from __future__ import annotations

from datetime import datetime

import fast_order
import guard
import router
from router import TakeMode

# 取餐方式 → 中文标签（与 render._METHOD_LABEL 同源，但 workflow 不直接依赖 render 私有成员）
MODE_LABEL = {"pickup": "到店自取", "dt": "得来速", "delivery": "麦乐送", "group": "企业团餐"}

METHOD_TO_MODE = {
    "pickup": TakeMode.PICKUP,
    "dt": TakeMode.DT,
    "delivery": TakeMode.DELIVERY,
    "group": TakeMode.GROUP,
}


class WorkflowError(Exception):
    """编排层失败（确定性校验失败或必需字段缺失）。message 直接可展示给用户。"""


def _money(v: float) -> str:
    s = f"{v:.2f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def _extract_stores(resp: dict) -> list[dict]:
    data = resp.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("stores", "list", "storeList"):
            if isinstance(data.get(k), list):
                return data[k]
    return []


def _extract_be_code(stores: list[dict], method: str) -> str | None:
    if method == "pickup":
        return None  # 到店场景 beCode 不传（传了报错）
    for s in stores:
        code = s.get("beCode")
        if code:
            return str(code)
    raise WorkflowError(f"{method} 场景必须从 query-stores 响应拿到 beCode，但响应中未包含（将触发 60002）")


def _extract_gm_service_code(resp: dict) -> str:
    """从 query-meal-assistance 响应取助餐服务码 gmServiceCode。

    团餐（beType=6）的 calculate-price / create-order 都**必须**带 gmServiceCode，
    而它只能来自 query-meal-assistance（须在算价之前查）。优先取 selected=True 的，
    否则取第一个 enable=True 的；都没有则失败显性化。
    """
    data = resp.get("data", {})
    items = data.get("mealAssistanceItems") or []
    if not isinstance(items, list):
        raise WorkflowError("query-meal-assistance 响应结构异常（mealAssistanceItems 非列表）")
    chosen = next((it for it in items if it.get("selected")), None)
    if chosen is None:
        chosen = next((it for it in items if it.get("enable")), None)
    if chosen is None:
        raise WorkflowError("query-meal-assistance 未返回可选助餐服务，无法取得 gmServiceCode（团餐下单必填）")
    code = chosen.get("gmServiceCode")
    if not code:
        raise WorkflowError("mealAssistanceItems 中缺少 gmServiceCode 字段")
    return str(code)


def _extract_take_way_code(resp: dict) -> str:
    data = resp.get("data", {})
    twl = data.get("takeWayList") or []
    if not twl:
        raise WorkflowError("calculate-price 未返回 takeWayList，无法取得 takeWayCode（下单必填，禁止自造）")
    first = twl[0]
    code = first.get("code") if isinstance(first, dict) else first
    if not code:
        raise WorkflowError("takeWayList 中缺少 code 字段")
    return str(code)


def _num(v) -> float:
    try:
        return float(str(v).replace("¥", "").strip())
    except (ValueError, TypeError):
        return 0.0


def _extract_price(resp: dict) -> dict:
    data = resp.get("data", {})
    # 真实 MCP 以「分」为单位（如 950 表示 ¥9.50），且优先字段是 originalPrice / price。
    # 这里统一换算成「元」，下游渲染与展示都用元。
    raw_original = (
        data.get("originalPrice")
        or data.get("productOriginalPrice")
        or data.get("totalPrice")
        or data.get("original")
        or 0
    )
    raw_pay = (
        data.get("price")
        or data.get("productPrice")
        or data.get("payPrice")
        or data.get("actualPrice")
        or data.get("pay")
        or data.get("payAmount")
        or 0
    )
    original = _num(raw_original) / 100.0
    pay = _num(raw_pay) / 100.0
    disc = data.get("discount") or data.get("discountAmount")
    raw_discount = (
        round(original - pay, 2) if disc in (None, "") else _num(disc) / 100.0
    )
    # 真实接口 discount 常为 0，无意义时不展示（render 也会跳过 0）
    discount = max(raw_discount, 0.0)
    if original <= 0 or pay <= 0:
        raise WorkflowError("calculate-price 返回的金额异常（original/pay 缺失或为零），已失败显性化")
    return {"original": original, "discount": discount, "pay": pay}


def _extract_order_id(resp: dict) -> str:
    data = resp.get("data", {})
    oid = data.get("orderId") or (data.get("order") or {}).get("orderId")
    if not oid:
        raise WorkflowError("create-order 响应未包含 orderId（无法回执/查询），已失败显性化")
    return str(oid)


def _nearby_args(method: str, city: str | None, keyword: str | None) -> dict:
    be_type = {"pickup": 1, "dt": 5, "delivery": 2}[method]
    # query-nearby-stores / delivery-query-stores 是"发现门店"调用，不需要 storeCode
    # （storeCode 在拿到门店后才用于计价/下单）
    args: dict = {"beType": be_type, "searchType": 2}
    if city:
        args["city"] = city
    if keyword:
        args["keyword"] = keyword
    return args


def _build_order_preview(method: str, plan: dict, items: list[dict], price: dict) -> dict:
    eta_label = f"预计 {plan['eta_minutes']} 分钟{'送达' if method in ('delivery', 'group') else '到店'}"
    return {
        "take_mode": MODE_LABEL[method],
        "eta": eta_label,
        "items": [{"name": it.get("name", it.get("code", "")), "qty": it.get("qty", 1)} for it in items],
        "price": {
            "original": _money(price["original"]),
            "discount": _money(price["discount"]),
            "discount_reason": "",
            "pay": _money(price["pay"]),
        },
    }


def run_fast_order(
    client,
    context: dict,
    items: list[dict],
    *,
    now: datetime | None = None,
    confirmed: bool = False,
    user_confirmation: str | None = None,
    city: str | None = None,
    keyword: str = "",
    stores: list[dict] | None = None,
    amount_limit: float | None = None,
) -> dict:
    """端到端极速点单编排。返回可直接喂给 render.render_message 的 dict。

    client : 具备 .call(name, args) 的对象（线上 MCDClient / 测试 FakeClient）
    context: {user_specified, is_driving, has_address, address_id}
    items  : [{"code", "qty", "name"?}]
    city   : 城市名；由 location.detect_city() 按 IP 自动定位后传入，不向用户询问。
    stores : 可选，已查到的门店列表。**传入时按调用方顺序取第一家**（不再按距离重选），
             因此请把「已用于选商品的那家店」放在首位，保证选品门店 == 计价门店；
             不传则由 workflow 自行查询并按业务规则选店。

    两阶段：confirmed=False 只出「方案 + 算价预览」（不写）；confirmed=True 才过闸门下单。
    user_confirmation：用户在对话中明确说出的确认原话（如「下单」「确认」）。confirmed=True
                       时必填，由 guard 校验是否含明确意图——禁止模型自行置 True。

    amount_limit：单次金额安全上限（元）。默认 None 表示按取餐方式动态兜底
    （guard.limit_for_method：团餐抬高到 5000，其余 200）；显式传入时以传入为准。
    """
    now = now or datetime.now()
    method = fast_order.choose_pickup_method(context)
    mode = METHOD_TO_MODE[method]
    stores_injected = stores is not None  # 调用方注入门店列表时，以调用方顺序为准

    # 单次金额上限：按取餐方式动态兜底（团餐抬高，避免 ¥300 起送单被默认 200 误拦）；
    # 显式传入 amount_limit 时以传入为准。
    if amount_limit is None:
        amount_limit = guard.limit_for_method(method)

    # 1. 取门店 + beCode
    # 到店/得来速：stores 可注入（保证"选品门店==计价门店"）；否则查 query-nearby-stores
    # 麦乐送/团餐：始终查 delivery-query-stores（按 beType 区分 2 / 6），且都需要 addressId
    address_id = context.get("address_id")
    if method == "delivery":
        if not address_id:
            raise WorkflowError("麦乐送需先提供 addressId（用 delivery-query-addresses 获取）")
        stores = _extract_stores(
            client.call("delivery-query-stores", {"addressId": address_id, "beType": 2})
        )
    elif method == "group":
        if not address_id:
            raise WorkflowError("企业团餐需先提供 addressId（用 delivery-query-addresses 获取）")
        stores = _extract_stores(
            client.call("delivery-query-stores", {"addressId": address_id, "beType": 6})
        )
    elif stores is None:
        # searchType=2 要求 city 与 keyword 都非空，否则真实接口报 600058。
        # 未提供关键词时回退到城市名，至少保证能搜到该城市的门店（就近选店仍按距离）。
        kw = keyword or city
        stores = _extract_stores(
            client.call("query-nearby-stores", _nearby_args(method, city, kw))
        )
    be_code = _extract_be_code(stores, method)

    # 2. 极速方案（选店 + ETA + 预约）
    # 调用方注入 stores 时，按调用方顺序以第一家为准（它通常就是已用于选品的那家店）；
    # 未注入时由 build_fast_order_plan 按业务规则自动选店。
    pinned = stores[0] if (stores_injected and method in ("pickup", "dt") and stores) else None
    plan = fast_order.build_fast_order_plan(stores, context, now, store=pinned)
    if plan.get("error"):
        return {"fast_plan": plan}  # render 软提示，不继续下单

    # 3. 算价（必须；takeWayCode 只在这里拿）
    route = router.resolve(mode)
    store_code = plan["store"]["storeCode"]
    resv = plan.get("reservation")
    reservation_date = resv["reservationDate"] if resv else None

    # 团餐专项：calculate-price / create-order 必须带 gmServiceCode，
    # 而 gmServiceCode 只能来自 query-meal-assistance（须在算价之前查）。
    gm_service_code = None
    if method == "group":
        assist_resp = client.call(
            "query-meal-assistance",
            {"storeCode": store_code, "beType": 6, "orderType": 2, "beCode": be_code},
        )
        gm_service_code = _extract_gm_service_code(assist_resp)

    price_args = router.build_args(
        route,
        store_code=store_code,
        items=items,
        be_code=be_code,
        gm_service_code=gm_service_code,  # 仅团餐传；其它方式为 None，router 自动跳过
        require_required=False,  # 计价是只读调用，不需要 addressId 这类仅下单才必填的字段
    )
    price_resp = client.call("calculate-price", price_args)
    # 外送/团餐不传 takeWayCode，故不强制提取；到店/得来速必须（下单必填）
    take_way_code = _extract_take_way_code(price_resp) if method not in ("delivery", "group") else None
    price = _extract_price(price_resp)

    order = _build_order_preview(method, plan, items, price)

    # 预览阶段：不写，返回方案 + 算价 + 确认召唤
    if not confirmed:
        return {"fast_plan": plan, "order": order, "action": {"confirm": True, "cancellable_min": 10}}

    # 4. 确认阶段：过闸门 → 下单
    order_args = router.build_args(
        route,
        store_code=store_code,
        items=items,
        be_code=be_code,
        take_way_code=take_way_code if method not in ("delivery", "group") else None,
        reservation_date=reservation_date,
        address_id=address_id if method in ("delivery", "group") else None,
        gm_service_code=gm_service_code,  # 仅团餐传
    )
    required = {
        "pickup": (),
        "dt": ("beCode",),
        "delivery": ("addressId",),
        "group": ("addressId", "gmServiceCode"),
    }[method]
    if not user_confirmation:
        raise WorkflowError(
            "下单前必须由用户在对话中明确确认（如「下单」「确认」），并把原话传入 "
            "user_confirmation；禁止模型自行置 confirmed=True。"
        )
    guard.require(
        "create-order",
        order_args,
        confirmed=True,
        user_said=user_confirmation,
        amount=price["pay"],
        required_fields=required,
        amount_limit=amount_limit,
    )

    order_resp = client.call("create-order", order_args)
    order_id = _extract_order_id(order_resp)
    return {
        "fast_plan": plan,
        "order": order,
        "receipt": guard.receipt_notice(price["pay"]),
        "order_id": order_id,
    }
