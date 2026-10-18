# 输出强制（Enforcement）

> 先说结论：**prompt 层的"必须"不是保证。** 真正的硬闸门只有宿主 hook；
> skill 能做的是"让合规路径最短 + 可校验 + 可度量"。

## L1 路径最短（高概率）

`python scripts/reply.py --json '<result>'` 直接输出成品消息，agent 只负责原样转发。

- 为什么有效：模型不用自己排版，抄比写省事。
- 局限：模型仍可能不用它（DeepSeek 实测就没用 `render.py`）。

## L2 自检（可检测）

`python scripts/validate_reply.py --file reply.txt` → `{"ok":..., "errors":[...]}`，不合规退出 1。

校验规则：主块图标、禁用词、原价/实付、`优惠 -¥`、回执 storeCode、≤18 行。

- 局限：模型可以跳过自检；所以它是"可审计"，不是"强制"。

## L3 宿主 hook（唯一硬保证）

`hooks/validate_reply_hook.py`：读待发送文本 → 跑 validator → 非 0 退出。

- **WorkBuddy**：在 hooks 配置里把 pre-response / pre-send 事件指到该脚本（字段以宿主版本文档为准）。
- **DSH**：同样接到发送前事件；若宿主不支持 hook，本层不可用。
- 注意：hook 的输入方式（stdin / 环境变量）与退出码语义由宿主决定，按宿主文档适配。

## L4 度量（可量化）

`python evals/format_compliance.py evals/replies.jsonl`

- 输入：`{"prompt": "...", "reply": "..."}` 每行一条（模型回复由你另行采集）。
- 输出：合规率 + 每条失败原因。
- 用法：改完 skill 后，用 `evals/prompts.json` 里的 prompt 重跑，比较合规率。

## 现实边界

- 宿主不支持 hook：只能做到 L1+L2+L4（高概率 + 可审计），无法 100%。
- 宿主支持"工具结果直接渲染"：把 `reply.py` 的输出当最终消息，agent 不写文字——这是最强形态。
