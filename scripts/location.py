#!/usr/bin/env python3
"""按 IP 自动定位所在城市（仅标准库，不询问用户）。

依次尝试中文 / JSON 两个公开 IP 定位接口；可用 ``MCD_GEO_ENDPOINT`` 指定单一接口。
只取城市名，**不保存 IP**。定位失败返回 ``None``，由调用方决定是否请用户确认——绝不编造位置。
"""
from __future__ import annotations

import json
import os
import urllib.request

# 顺序即优先级：先中文接口（返回「某省 某市」这类地名），失败再用 JSON 接口兜底。
_ENDPOINTS = (
    "https://myip.ipip.net",
    "https://ipapi.co/json/",
    "http://ip-api.com/json/?lang=zh-CN&fields=status,city,regionName,country",
)


def _http_get(url: str, timeout: float) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def _parse_city(text: str) -> str | None:
    # 中文文本：形如「当前 IP：x.x.x.x  来自于：中国 某省 某市  运营商」
    if "来自于：" in text:
        parts = text.split("来自于：", 1)[1].strip().split()
        if len(parts) >= 3:
            return parts[2]
    # JSON：ipapi.co / ip-api.com
    try:
        data = json.loads(text)
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("status") == "fail":
        return None
    city = data.get("city") or data.get("regionName") or data.get("region")
    return city.strip() if isinstance(city, str) and city.strip() else None


def detect_city(timeout: float = 5.0) -> str | None:
    """返回 IP 定位到的城市名；失败返回 None。"""
    custom = os.environ.get("MCD_GEO_ENDPOINT")
    endpoints = (custom,) if custom else _ENDPOINTS
    for url in endpoints:
        try:
            city = _parse_city(_http_get(url, timeout))
        except Exception:
            continue
        if city:
            return city
    return None


def confirm_prompt(city: str | None) -> str:
    """定位成功后的轻度确认话术；失败时才请用户补充。"""
    if not city:
        return "没能自动定位到城市，方便告诉我你在哪个城市吗？"
    return f"我按你的网络位置定位到「{city}」，不对的话告诉我城市名就行。"
