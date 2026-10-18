#!/usr/bin/env python3
"""当前时间（自动获取，绝不询问用户）。

优先用 MCP ``now-time-info`` 的服务器时间；不可用时回退本地时钟。
"""
from __future__ import annotations

from datetime import datetime


def now(client=None) -> datetime:
    """返回当前时间。

    client 具备 ``.call(name, args)`` 时，优先取 MCP ``now-time-info``（真实服务器时间），
    解析失败或不可用则回退到本机时钟。两种情况都**不需要向用户询问**。
    """
    if client is not None:
        try:
            resp = client.call("now-time-info", {})
            data = resp.get("data", {}) if isinstance(resp, dict) else {}
            text = data.get("formatted") or data.get("datetime")
            if isinstance(text, str) and text:
                try:
                    return datetime.fromisoformat(text)
                except ValueError:
                    return datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
        except Exception:  # 无网络 / 工具不可用时静默回退
            pass
    return datetime.now()
