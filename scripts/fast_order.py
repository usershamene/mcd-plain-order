"""极速点单编排：按总耗时选店 + 预约到店即取。

确定性决策（Rule 5）：选方式 / 选店 / 算预约时间都是代码，不交模型。
网络调用（query-nearby-stores / calculate-price / create-order）由 mcd_client 在 workflow 中完成。

已核实的真实字段（探针数据 + 工具文档）：
- 门店：distance(米), businessStatus(bool), reservation(bool), reservationTimeOptions[]
- 预约：create-order 支持 reservationDate(yyyy-MM-dd HH:mm)；query-meals 预约时也传
- reservationTimeOptions[].reservationOptionText 形如
  "早餐(06:14至10:15)，午餐(10:44至14:15)，下午茶(14:44至16:45)，夜市(17:14至21:45)，宵夜(22:14至22:46)"
"""
from __future__ import annotations

from datetime import datetime, timedelta

# 估算速度（米/分钟）：步行与驾车（市区保守值）。用于相对耗时比较与预约 ETA 估计。
_SPEED = {"walking": 70, "driving": 250}


def choose_pickup_method(context: dict) -> str:
    """按上下文决定取餐方式。
    context: {user_specified, is_driving, has_address}
    优先级：用户明确指定 > 开车(得来速) > 有地址(麦乐送) > 默认到店自取。
    极速场景默认到店自取或得来速（最快），外送更慢，不主动选。
    """
    spec = context.get("user_specified")
    if spec in ("pickup", "dt", "delivery", "group"):
        return spec
    if context.get("is_driving"):
        return "dt"
    if context.get("has_address"):
        return "delivery"
    return "pickup"


def select_nearest_store(stores: list[dict], prefer_dt: bool = False) -> dict | None:
    """挑最优店：仅营业中且支持预约，按 distance 升序；prefer_dt 时优先「得来速」店名。
    distance 为米，越小越快。
    """
    open_stores = [s for s in stores if s.get("businessStatus") and s.get("reservation")]
    if prefer_dt:
        dt = [s for s in open_stores if "得来速" in s.get("storeName", "")]
        if dt:
            open_stores = dt
    if not open_stores:
        return None
    return min(open_stores, key=lambda s: float(s.get("distance", 1e9)))


def _to_hm(s: str) -> tuple[int, int]:
    h, m = s.split(":")
    return int(h), int(m)


def parse_windows(option_text: str) -> list[tuple[str, str, str]]:
    """解析 reservationOptionText → [(时段名, 起, 止), ...]。
    例：'早餐(06:14至10:15)，午餐(10:44至14:15)' → [('早餐','06:14','10:15'), ('午餐','10:44','14:15')]
    """
    out = []
    for seg in option_text.split("，"):
        if "(" in seg and ")" in seg:
            name = seg[: seg.index("(")]
            inner = seg[seg.index("(") + 1 : seg.index(")")]
            if "至" in inner:
                start, end = inner.split("至")
                out.append((name, start.strip(), end.strip()))
    return out


def pick_reservation_window(
    now: datetime, eta_minutes: int, reservation_time_options: list[dict], buffer: int = 5
) -> dict | None:
    """算预约时间 = now + eta + 缓冲，落在一个有效时段内。
    返回 {date, reservationDate(yyyy-MM-dd HH:mm), window, adjusted} 或 None（当天无法预约）。
    adjusted=True 表示 target 落在时段间隙，已顺延到下一时段起点。
    """
    if not reservation_time_options:
        return None
    opt = next((o for o in reservation_time_options if o.get("today")), reservation_time_options[0])
    windows = parse_windows(opt.get("reservationOptionText", ""))
    if not windows:
        return None

    target = now + timedelta(minutes=eta_minutes + buffer)
    th, tm = target.hour, target.minute
    fallback = None  # 第一个晚于 target 的时段起点
    for name, s, e in windows:
        sh, sm = _to_hm(s)
        eh, em = _to_hm(e)
        if (sh, sm) <= (th, tm) <= (eh, em):
            return {
                "date": opt["date"],
                "reservationDate": target.strftime("%Y-%m-%d %H:%M"),
                "window": name,
                "adjusted": False,
            }
        if (sh, sm) > (th, tm) and fallback is None:
            fallback = (name, s)
    if fallback:
        return {
            "date": opt["date"],
            "reservationDate": f"{opt['date']} {fallback[1]}",
            "window": fallback[0],
            "adjusted": True,
        }
    return None  # target 落在所有时段之后，当天无法预约


def estimate_eta_minutes(distance_m: float, mode: str = "walking") -> int:
    """按距离估 ETA（分钟）。mode: walking / driving。至少 1 分钟。"""
    sp = _SPEED.get(mode, 70)
    return max(1, round(distance_m / sp))


def build_fast_order_plan(
    stores: list[dict],
    context: dict,
    now: datetime,
    buffer: int = 5,
    store: dict | None = None,
) -> dict:
    """组装极速点单方案（确定性部分）。workflow 据此调用 calculate-price + create-order。
    返回 {store, method, eta_minutes, reservation}；无可用店时 {error}。

    store：调用方已选定的门店（通常已用它取过菜单）。传入时**直接采用、不再重选**，
    以保证「选品门店 == 计价门店」；不传时才按业务规则自动选店。
    """
    method = choose_pickup_method(context)
    store = store or select_nearest_store(stores, prefer_dt=(method == "dt"))
    if not store:
        return {"error": "附近无营业且支持预约的门店"}
    dist = float(store.get("distance", 0))
    mode = "driving" if method == "dt" else "walking"
    eta = estimate_eta_minutes(dist, mode)
    # 预约到店仅适用于到店自取 / 得来速；麦乐送是送上门，不挂门店预约时段。
    if method in ("pickup", "dt"):
        resv = pick_reservation_window(now, eta, store.get("reservationTimeOptions", []), buffer=buffer)
    else:
        resv = None
    return {
        "store": {
            "storeCode": store.get("storeCode"),
            "storeName": store.get("storeName"),
            "distance": dist,
        },
        "method": method,
        "eta_minutes": eta,
        "reservation": resv,
    }
