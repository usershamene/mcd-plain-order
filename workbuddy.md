# WorkBuddy 开发对话上下文

> 本文件用于证明本项目真实使用了腾讯 WorkBuddy 智能体开发，是参与 WorkBuddy 专项奖励（排行榜前 100 名可得 3000 积分）的必备材料。
> 文件名固定为 `workbuddy.md`，不可改动。

## 项目信息

| 项目 | 内容 |
|---|---|
| 项目名称 | 麦麦明白点 · mcd-plain-order |
| 项目定位 | 对话式麦当劳点餐助手，覆盖全流程，对齐工信部适老化设计规范 |
| 开发工具 | 腾讯 WorkBuddy（Agent 模式） |
| 开发起始时间 | 2026-10-10 |

## 使用 WorkBuddy 完成的工作（时间线）

| 时间 | 完成内容 |
|---|---|
| 10-10 | 调研大赛规则与提交要求；核对出**必备文件为 5 个**（含易被忽略的 `mcp-config.example.json`） |
| 10-10 | 搭建参赛仓库骨架（README / CONTEST_DECLARATION / MCP_INTEGRATION / mcp-config.example.json / workbuddy.md） |
| 10-10 | 在 WorkBuddy 中配置 mcd-mcp 自定义连接器；**排查并修复启用失败**（定位为 Authorization 缺少 `Bearer ` 前缀，修复后握手验证通过） |
| 10-10 | 直连 MCP 服务器完成 **35 个工具深度调研**：拉取全量 schema、实测 20 个只读工具的真实返回，产出 `references/mcd-mcp-tools-reference.md` |
| 10-11 | 产出商家（B 端）方向调研报告；核查赛事排行榜约 120 个上榜项目的方向分布 |
| 10-11 | 产出 C 端方向借鉴报告：拆解 11 个头部作品 README，提炼 8 条可复制模式；调研千问生态（肯德基/瑞幸/蜜雪）与星巴克的做法 |
| 10-11 | 产出适老化方向调研报告：核实工信部适老化规范硬指标与银发群体真实数据 |
| 10-11 | **用数据修正定位**：银发线上点餐参与率不足 20%、点过外卖仅 12.5% → 结论「适老化作为人群定位不成立，作为设计标准成立」，最终定为「普惠 + 适老设计」 |
| 10-11 | 以真实地理位置（宁波）与日期跑通 **14 次真实 MCP 调用**，产出链路探查报告；发现**券是日内时段券**这一关键事实 |
| 10-11 | 完成产品定义、适老设计标准（可验收清单）、六层架构设计 |
| 10-11 | 实现确定性核心模块：`router.py`（场景路由）、`coupons.py`（券窗口判定）、`guard.py`（写操作闸门）+ `tests/test_core.py`（**28 个测试全部通过**） |
| 10-11 | 用真实抓取的 13 张券验证券判定逻辑，并发现**11/13 张券不支持外送**这一交叉约束 |

## WorkBuddy 中使用的关键能力

- **连接器（自定义 MCP）**：接入麦当劳 MCP Server，在对话中直接调用 35 个业务工具
- **Skill 开发**：按 WorkBuddy Skill 规范（SKILL.md + scripts/ + references/）实现作品本体
- **脚本协同与验证**：在 WorkBuddy 中编写并实跑 `mcd_client.py` / `router.py` / `coupons.py` / `guard.py` 与单测
- **联网调研**：检索赛事排行榜、竞品仓库、行业报道与政策文件

## 开发过程中的关键技术发现（由 WorkBuddy 实测得出）

1. 统一响应信封为 `{success, code, message, datetime, traceId, data}`，与工具声明的 `outputSchema` 不一致
2. **业务失败不通过 MCP 错误机制上报**，必须显式校验 `success` 字段，否则会把失败误判为成功
3. `beType / orderType / beCode` 三者强耦合，配对错误返回 `code=60002`；到店场景传 `beCode` 会直接报错
4. **券是「日内时段券」**：实测早餐券仅当天 05:00–10:29 有效，过期即作废 → 提醒指标应为「距窗口关闭的分钟数」
5. 券的 `weekdays` 字段语义官方未说明，经 12 个数据点反推为 **1=周日、2=周一 … 7=周六**
6. 券存在 `onlyAppCanRedeem` / `onlyPosCanRedeem` 渠道限制，此类券在对话内无法核销
7. 卡包券 ≠ 门店可用券（实测 13 : 1）；且 **11/13 张券不支持外送**
8. 积分商城按 `shopId` 分流：`2` = 积分兑换券，`5` = 现金付费活动（`point=0`），对应两个不同的下单工具
9. 部分接口返回体极大（`query-my-coupons` 57KB），必须做客户端字段裁剪
10. `list-nutrition-foods` 返回的是压缩表格字符串而非 JSON

## 补充说明

WorkBuddy 支持导出对话上下文。建议在本文件后附上导出的原始对话记录，以进一步佐证真实使用过程。

> ⚠️ 导出时务必确认其中**不含真实 MCP Token、手机号、地址**等敏感信息。

## 实现细节与联调校准（从 SKILL.md 迁出的开发记录）

> 以下为开发 / 验证过程记录，**不属于技能运行规范**（运行规范见 `SKILL.md`），仅用于佐证实现与真实联调。

### 极速点单端到端编排（`workflow.run_fast_order`）

`workflow.run_fast_order(client, context, items, now, confirmed, stores=...)` 把「选店 → 算价 → 下单」串起来，所有确定性决策（路由 / 方案 / 金额映射 / 字段提取）写死在代码里，网络调用全部委托给注入的 client。

- **测试 / 演示**：用 `tests/test_workflow.py` 的 `FakeClient` 回放真实探针 + 模拟计价 / 下单，不依赖真实 Token 即可确定性跑通；专门验证 beCode 配对、takeWayCode 来源、reservationDate、orderId 不生成等风险点。
- **接真实 MCP**：`examples/online_demo.py` 检测到 `MCD_MCP_TOKEN` 即用真实 `MCDClient`，否则回退 `FakeClient`（调用契约一致）。
- **两阶段**：`confirmed=False` 只出预览（不写）；`confirmed=True` 才过 `guard` 闸门下单。
- **只读安全约定**：live 默认只跑只读预览，绝不自动下单；真下单需显式 `--really-order`，且脚本只对「到店自取」单一场景下单。

### 真·联调校准结论（已用真实 Token 验证）

`examples/smoke_live.py` 用真实 Token 跑通只读预览，实测修正了以下字段 / 约束：

| 项 | 实测结论 | 处理 |
| --- | --- | --- |
| 商品字段 | `productCode` + `quantity`（不是 code / qty）——传错会 600010 或静默空计价 | `router.build_args` 在边界统一转换 |
| 金额单位 | **分**（`originalPrice:1950` = ¥19.5） | `workflow._extract_price` 统一 `/100` |
| 优惠字段 | 顶层 `discount`（分）；无优惠为 0 | 渲染时 `discount<=0` 不显示「优惠」行 |
| takeWayCode | `takeWayList[].code`（如 `eat-in`） | `workflow._extract_take_way_code`，外送不传 |
| `calculate-price` 入参 | **不传 reservationDate**；beType=5/6 必传 beCode（600048） | 计价只传 items / storeCode / beType / orderType（+beCode） |
| `query-nearby-stores` | searchType=2 时 city 与 keyword 都必填（600058）；同参两次可能返回不同门店 | 缺 keyword 回退城市名；`run_fast_order(..., stores=...)` 支持注入，保证「选品门店 == 计价门店」 |
| DT 门店 | beType=5 的 `query-nearby-stores` 会返回 beCode | `_extract_be_code` 取自响应，缺失即显式报错 |

真联调现状：到店自取（只读预览）+ 麦乐送、团餐（均已真实下单，验证全链路）+ 得来速（真实只读预览验证 beCode 配对，beCode=`333054841`）已用真实 Token 跑通。

### 企业团餐（group）链路

`workflow.run_fast_order` 支持第四种取餐方式 `group`（beType=6 / orderType=2）：

```
delivery-query-stores(addressId, beType=6) → storeCode + beCode
  ↓
query-meal-assistance(storeCode, beType=6, orderType=2, beCode) → gmServiceCode   （须在 calculate-price 之前）
  ↓
calculate-price(..., beCode, gmServiceCode) → 金额
  ↓
create-order(..., beCode, gmServiceCode, addressId)
```

- `gmServiceCode` **只能**来自 `query-meal-assistance`（优先 `selected=True` 项，否则第一个 `enable=True`），缺失即失败显性化。
- 团餐与麦乐送同为 orderType=2，**不传** takeWayCode / reservationDate。
- 团餐有 ¥300 起送门槛；真实下单曾返回 `[499] ¥300元起送`。

### 金额上限（guard）按取餐方式动态化

`guard.limit_for_method(method)`：企业团餐 ¥5000（含起送门槛，避免被默认 ¥200 误拦），其余 ¥200；`run_fast_order(amount_limit=None)` 时自动按方式兜底，显式传入优先。
