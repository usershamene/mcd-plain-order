"""标准化输出渲染。

确定性模板（对齐仓库 Rule 5：输出格式禁止交给模型）。
模型只负责组装结构化数据（点什么、多少钱、哪些提醒），render 负责把它变成
统一、克制、信息密度高的消息。

设计原则：
- 顺序固定：先给答案（点单）→ 再给补充（提醒）→ 最后给行动（确认）。不写"为您查询到"之类前戏。
- 图标即标签：🍔 点单 / 🆕 新品 / 🎫 券 / ⭐ 积分 / 👉 行动。省去"以下是新品提醒"这类废话。
- 不留空段：某段没有内容就不显示，不凑数。
- 大白话：写"优惠 / 用了券"，不写"折扣 / 核销 / 随心配 / 券包"。
- 数字优先：金额、剩余分钟直接给数，不给"一些 / 很快"。

分区靠两层机制区分：① 每段用「图标 + 短标签」做标题；② 段与段之间空一行。
"""
from __future__ import annotations


def render_greeting(text: str | None) -> str:
    """问候/上下文行，最多一行，仅在有价值时给。"""
    if not text:
        return ""
    return f"👋 {text}"


def render_order(order: dict) -> str:
    """核心点单块：取餐方式 + 餐品清单 + 三行金额（原价/优惠/实付）。"""
    if not order:
        return ""
    mode = order.get("take_mode", "")
    eta = order.get("eta", "")
    head = "🍔 为你推荐"
    if mode or eta:
        head += "（" + " · ".join(x for x in (mode, eta) if x) + "）"
    lines = [head]
    for it in order.get("items", []):
        qty = it.get("qty", 1)
        name = it.get("name", "")
        lines.append(f"· {name} ×{qty}")
    price = order.get("price", {})
    if price:
        lines.append("─" * 12)
        lines.append(f"原价 ¥{price.get('original', '')}")
        disc = price.get("discount", "")
        try:
            disc_v = float(str(disc))
        except (ValueError, TypeError):
            disc_v = 0.0
        if disc_v > 0:
            reason = price.get("discount_reason", "")
            lines.append(f"优惠 -¥{disc}" + (f"（{reason}）" if reason else ""))
        lines.append(f"实付 ¥{price.get('pay', '')}")
    return "\n".join(lines)


_METHOD_LABEL = {"pickup": "到店自取", "dt": "得来速", "delivery": "麦乐送", "group": "企业团餐"}


def render_fast_plan(plan: dict, detail: bool = False) -> str:
    """极速点单方案块：取餐方式 + 最近门店 + 预约到店时间（确定性渲染）。

    detail=True 时表示方案已依附在「为你推荐」点单块下方，作为取餐安排子块，
    不再重复方式/ETA 头（推荐块已含），也不再用第二个 🍔，避免两个 🍔 撞脸。
    无可用门店（plan 带 error）时软提示，不静默丢块、也不强制推荐。
    """
    if not plan:
        return ""
    err = plan.get("error")
    if err:
        return f"🍔 极速点单\n· {err}，换个时间或地点再试？"
    method = plan.get("method", "pickup")
    label = _METHOD_LABEL.get(method, "到店自取")
    eta = plan.get("eta_minutes", 0)
    eta_label = f"预计 {eta} 分钟{'送达' if method in ('delivery', 'group') else '到店'}"
    store = plan.get("store", {})
    name = store.get("storeName", "")
    dist = store.get("distance")

    if detail:
        # 依附在点单块下：用「取餐安排」子标题，避免与推荐块叠两个 🍔
        lines = ["📍 取餐安排"]
    else:
        lines = [f"🍔 极速点单（{label} · {eta_label}）"]

    if name:
        dist_s = f"（{_fmt(dist)} 米）" if dist is not None else ""
        lines.append(f"· 最近门店：{name}{dist_s}")
    # 麦乐送/团餐为送上门（或到店自提团餐），不挂门店预约时段，标题已给"预计 N 分钟送达"。
    if method not in ("delivery", "group"):
        resv = plan.get("reservation")
        if resv:
            rd = resv.get("reservationDate", "")
            win = resv.get("window", "")
            adj = "已顺延至本时段起点" if resv.get("adjusted") else "到店即取"
            lines.append(f"· 预约到店：{rd}（{win}时段，{adj}）")
        else:
            # 门店营业但今天预约时段已结束（或门店未给时段）：预约非必需，可直接到店。
            lines.append("· 今日预约时段已过，可直接到店")
    return "\n".join(lines)


def render_new(items: list[dict]) -> str:
    """新品提醒；命中用户"喜欢"的品类时，在该行末尾加"· 你喜欢"，标题保持中性。"""
    if not items:
        return ""
    lines = ["🆕 新品"]
    for it in items:
        tag = it.get("tag", "上新")
        line = f"· {it.get('name', '')} ¥{it.get('price', '')}（{tag}）"
        if it.get("liked"):
            line += " · 你喜欢"
        lines.append(line)
    return "\n".join(lines)


def render_coupon(items: list[dict]) -> str:
    """优惠券提醒；带紧迫度（剩余分钟/天数）；命中用户「喜欢」的品类时加「· 你喜欢」。"""
    if not items:
        return ""
    lines = ["🎫 券提醒"]
    for c in items:
        urgency = c.get("urgency", "")
        line = f"· {c.get('title', '')}" + (f" · {urgency}" if urgency else "")
        if c.get("liked"):
            line += " · 你喜欢"
        lines.append(line)
    return "\n".join(lines)


def _fmt(v) -> str:
    """数值格式化：整数去尾零（180.0 → 180），非整数保留（180.7 → 180.7）。"""
    try:
        f = float(v)
    except (ValueError, TypeError):
        return str(v)
    return str(int(f)) if f.is_integer() else str(f)


def render_points(points: dict) -> str:
    """积分提醒；能换给"可换 X"，不能换给"还差多少分换 X"。"""
    if not points:
        return ""
    bal = points.get("balance")
    if bal is None:
        return ""
    near = points.get("near")  # {"name", "gap", "reachable"}
    if near:
        name = near.get("name", "")
        if near.get("reachable"):
            return f"⭐ 积分 · 当前 {_fmt(bal)} 分，可换「{name}」"
        gap = near.get("gap", 0)
        return f"⭐ 积分 · 当前 {_fmt(bal)} 分，还差 {_fmt(gap)} 分换「{name}」"
    return f"⭐ 积分 · 当前 {_fmt(bal)} 分"


def render_action(action: dict) -> str:
    """行动召唤：确认 + 可撤销提示（来自 guard 的可撤销规范）。"""
    if not action:
        return ""
    out = []
    if action.get("confirm"):
        mins = action.get("cancellable_min", 10)
        out.append("👉 确认下单？")
        out.append(f"· 下单后 {mins} 分钟内可取消（回复「确认」）")
    note = action.get("note")
    if note:
        out.append(f"· {note}")
    return "\n".join(out)


def render_message(result: dict) -> str:
    """按固定顺序装配消息，自动跳过空段。"""
    blocks = []
    g = render_greeting(result.get("greeting"))
    if g:
        blocks.append(g)
    o = render_order(result.get("order", {}))
    if o:
        blocks.append(o)
    fp = result.get("fast_plan")
    if fp:
        # 与推荐点单同框时降级为"取餐安排"子块，避免两个 🍔 撞脸
        fp_block = render_fast_plan(fp, detail=bool(o))
        if fp_block:
            blocks.append(fp_block)
    r = result.get("reminders", {})
    rn = render_new(r.get("new", []))
    if rn:
        blocks.append(rn)
    rc = render_coupon(r.get("coupon", []))
    if rc:
        blocks.append(rc)
    rp = render_points(r.get("points", {}))
    if rp:
        blocks.append(rp)
    a = render_action(result.get("action", {}))
    if a:
        blocks.append(a)
    rct = result.get("receipt")
    if rct:
        blocks.append(rct)
    return "\n\n".join(blocks)
