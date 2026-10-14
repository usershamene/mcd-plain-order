#!/usr/bin/env python3
"""麦当劳 MCP 调用客户端。

设计要点（对应 SKILL.md 中的强制规则）：
1. 麦当劳 MCP 的业务失败不通过 MCP 错误机制上报，而是返回 success=false。
   本模块把这种情况转成异常抛出，避免上层误判为成功。
2. 鉴权失败(401/400003)与限流(429)单独区分，便于上层做不同处理。

用法：
    python mcd_client.py list-tools
    python mcd_client.py call <tool-name> ['{"参数": "值"}']
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

MCP_URL = "https://mcp.mcd.cn"
TIMEOUT = 30


class MCPError(Exception):
    """MCP 协议层错误（网络、鉴权、限流）。"""


class BusinessError(Exception):
    """业务层失败：HTTP 正常但 success=false。"""

    def __init__(self, code, message, trace_id=None):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.trace_id = trace_id


def _token() -> str:
    token = os.environ.get("MCD_MCP_TOKEN", "").strip()
    if not token:
        raise MCPError("环境变量 MCD_MCP_TOKEN 未设置")
    if token.startswith("Bearer "):
        token = token[len("Bearer "):]
    return token


class MCDClient:
    def __init__(self, token: str | None = None):
        self.token = token or _token()
        self._session_id: str | None = None
        self._initialized = False

    def _post(self, method: str, params: dict | None = None) -> dict:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method}
        if params is not None:
            payload["params"] = params
        req = urllib.request.Request(
            MCP_URL, method="POST", data=json.dumps(payload).encode("utf-8")
        )
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json, text/event-stream")
        if self._session_id:
            req.add_header("Mcp-Session-Id", self._session_id)
        try:
            resp = urllib.request.urlopen(req, timeout=TIMEOUT)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            if exc.code == 401:
                raise MCPError(f"鉴权失败(401)：Token 无效或已过期。{body[:200]}") from exc
            if exc.code == 429:
                raise MCPError("触发限流(429)：超过 600 次/分钟，请降低调用频率") from exc
            raise MCPError(f"HTTP {exc.code}：{body[:200]}") from exc
        except urllib.error.URLError as exc:
            raise MCPError(f"网络错误：{exc.reason}") from exc

        session_id = resp.headers.get("Mcp-Session-Id") or resp.headers.get("mcp-session-id")
        if session_id:
            self._session_id = session_id

        raw = resp.read().decode("utf-8", "replace")
        for line in raw.splitlines():
            if line.startswith("data:"):
                raw = line[5:].strip()
                break
        return json.loads(raw)

    def _ensure_init(self) -> None:
        if self._initialized:
            return
        self._post(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "mcd-skill", "version": "1.0"},
            },
        )
        self._initialized = True

    def list_tools(self) -> list[dict]:
        self._ensure_init()
        result = self._post("tools/list", {})
        return result.get("result", {}).get("tools", [])

    def call(self, name: str, arguments: dict | None = None) -> dict:
        """调用 MCP 工具。业务失败会抛 BusinessError（不会静默返回失败结果）。"""
        self._ensure_init()
        result = self._post("tools/call", {"name": name, "arguments": arguments or {}})
        if "error" in result:
            raise MCPError(f"协议错误：{result['error']}")
        payload = result.get("result", {})
        data = payload.get("structuredContent") or payload.get("content") or {}
        if isinstance(data, list):  # content 为 [{type, text}] 时取文本
            data = data[0].get("text", "{}") if data else {}
            data = json.loads(data) if isinstance(data, str) else data
        if isinstance(data, dict) and data.get("success") is False:
            raise BusinessError(data.get("code"), data.get("message"), data.get("traceId"))
        return data


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    client = MCDClient()
    command = sys.argv[1]
    try:
        if command == "list-tools":
            for tool in client.list_tools():
                print(f"- {tool['name']}")
            return 0
        if command == "call":
            if len(sys.argv) < 3:
                print("缺少工具名", file=sys.stderr)
                return 2
            args = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
            print(json.dumps(client.call(sys.argv[2], args), ensure_ascii=False, indent=2))
            return 0
        print(f"未知命令：{command}", file=sys.stderr)
        return 2
    except (MCPError, BusinessError) as exc:
        print(f"调用失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
