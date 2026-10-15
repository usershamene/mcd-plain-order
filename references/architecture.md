# 架构设计

## 一、总原则

> **模型只做两件事：① 把用户的话抽成结构化槽位 ② 把计算结果说成人话。**
> **其余全部是显式代码：场景路由靠查表、方案生成靠枚举、比价靠算法、闸门靠条件判断。**

理由：全流程点餐 Skill 最容易坏在两处——① 场景判错（`beType/orderType/beCode` 配错必报 `60002`）；② 没做失败校验（麦当劳 MCP 的业务失败**不走 isError**，会被误判成成功）。这两件事靠模型"记得"是靠不住的。

---

## 二、六层架构

```
┌─ L0 槽位抽取（模型） ───────────────────────────────┐
│  「80块、两个人、能用券、到店取」                      │
│   → {budget:80, people:2, useCoupon:true, takeMode:null} │
└───────────────────────┬────────────────────────────┘
                        ↓
┌─ L1 场景路由（代码·查表）───────────────────────────┐
│  takeMode=null + 有地址 → 查路由表 → beType/orderType  │
│  并校验 beCode 来源与必填项                            │
└───────────────────────┬────────────────────────────┘
                        ↓
┌─ L2 数据编排（代码）───────────────────────────────┐
│  并行取：门店 → 菜单 → 门店可用券 → 活动 → 积分        │
│  关键职责：字段裁剪（券单次 38KB、日历 32KB）           │
└───────────────────────┬────────────────────────────┘
                        ↓
┌─ L3 决策引擎（代码·算法）───────────────────────────┐
│  候选枚举 → 逐个 calculate-price 真实计价 → 排序 → 对比表 │
└───────────────────────┬────────────────────────────┘
                        ↓
┌─ L4 安全闸门（代码·条件）───────────────────────────┐
│  写操作白名单 / 必填校验 / 金额上限 / 显式确认 / 可撤销   │
└───────────────────────┬────────────────────────────┘
                        ↓
┌─ L5 执行与回执（代码）──────────────────────────────┐
│  create-order → 支付链接 → 取餐信息 → 撤销说明          │
└────────────────────────────────────────────────────┘
```

---

## 三、模块职责与接口契约

### 3.1 `scripts/mcd_client.py`（已有）

MCP 调用客户端。**已完成并实测通过**。

| 能力 | 说明 |
|---|---|
| `MCDClient.call(tool, args)` | 调用工具；**业务失败抛 `BusinessError`**，协议/鉴权失败抛 `MCPError` |
| `MCDClient.list_tools()` | 列出全部工具 |
| 鉴权 | 从环境变量 `MCD_MCP_TOKEN` 读取，自动补 `Bearer ` |

### 3.2 `scripts/router.py`（新增）

**场景路由表** —— 纯查表，零模型参与。

```python
T = TakeMode.PICKUP | DT | DELIVERY | GROUP

resolve(take_mode, has_address) -> RoutePlan
RoutePlan = {be_type, order_type, need_be_code, be_code_source, extra_required}
validate(plan, args) -> None | raise RouteError
```

关键规则（来自实测）：
- 到店自取：`beCode` **不能传**（传了报错）
- 得来速：`beCode` 来自 `query-nearby-stores`
- 麦乐送/团餐：`beCode` 来自 `delivery-query-stores`；团餐额外需要 `gmServiceCode`

### 3.3 `scripts/coupons.py`（新增）

**券可用性判定**（确定性，可单测）。

```python
evaluate(coupon, now, take_mode) -> CouponStatus
CouponStatus = AVAILABLE | NOT_STARTED | EXPIRED | WRONG_CHANNEL | WRONG_TAKE_MODE
# AVAILABLE 时附带 remaining_minutes（距窗口关闭的分钟数）
```

判定顺序（必须按此顺序，先排除再计算）：
1. 渠道限制：`onlyAppCanRedeem` / `onlyPosCanRedeem` → `WRONG_CHANNEL`（提醒了也用不了）
2. 取餐方式：`beTypes` 是否包含当前 `beType` → `WRONG_TAKE_MODE`
3. 时间：`tradeEndDateTime < now` → `EXPIRED`；`tradeStartDateTime > now` → `NOT_STARTED`
4. 日内时段：`tradeTimePeriods` 与 `weekdays` 是否覆盖当前时刻
5. 剩余次数：`totalAvailableCount <= 0` → `EXPIRED`（已用完）

> 实测依据：券的有效期是**日内窗口**（如早餐券仅当天 `05:00–10:29`），所以核心指标是**距窗口关闭还有多久**，不是"还有几天到期"。

### 3.4 `scripts/planner.py`（下一轮）

候选生成与比价。

```python
build_candidates(menu, coupons, budget, people) -> list[Candidate]
price_all(client, candidates, route) -> list[Priced]   # 全部走 calculate-price
rank(priced) -> list[Priced]                            # 排序 + 生成对比表
```

**硬约束**：任何价格都必须来自 `calculate-price` 真实返回，**禁止本地估算**。若某个候选计价失败，必须标记为"计价失败"而不是静默丢弃。

### 3.5 `scripts/guard.py`（新增）

写操作闸门。

```python
WRITE_TOOLS = {create-order, cancel-order, mall-create-order,
               party-order-create, auto-bind-coupons,
               draw-lottery, delivery-create-address}

check(tool, args, *, confirmed: bool, amount=None) -> None | raise GuardError
```

规则：
1. 未显式确认 → 拦截（**默认必须为未确认**）
2. 金额超过阈值（默认 ¥200）→ 拦截并要求二次确认
3. 必填项缺失 → 拦截
4. `draw-lottery` 额外要求先展示 `drawDecision.nextConsumption`
5. 任何一次拦截都必须给出**为什么被拦**（失败显性化）

### 3.6 `scripts/render.py`（下一轮）

输出渲染，承载**设计标准**：
- 金额三行（原价 / 优惠 / 实付）
- 禁用词表扫描（`随心配`、`核销`… → 替换）
- 下单回执必含撤销话术
- 对比表渲染

---

## 四、数据流（点餐主链路）

```
用户一句话
  │
  ├─ L0 抽槽位 ─────────────────────────────► slots
  ├─ L1 路由 ──────────────────────────────► RoutePlan(beType/orderType/beCode)
  ├─ L2 并行取数
  │     ├─ query-nearby-stores / delivery-query-stores  → storeCode, beCode
  │     ├─ query-meals                                  → 餐品 code（唯一来源）
  │     ├─ query-store-coupons + query-my-coupons        → 可用券（两处都要查）
  │     └─ query-my-account                              → 积分
  ├─ L3 决策
  │     ├─ 候选枚举（单点/套餐/券组合）
  │     └─ calculate-price × N（真实计价）→ takeWayList[].code
  ├─ L4 闸门：展示三行金额 + 确认
  └─ L5 create-order → 支付链接 + 撤销说明
```

**关键依赖**：`storeCode` 来自 L2；`takeWayCode` **只能**来自 L3 的 `calculate-price`；`orderId` **只能**来自工具返回或用户输入。

---

## 五、错误处理策略

| 层 | 错误类型 | 处理 |
|---|---|---|
| 客户端 | 401 鉴权失败 | 提示 Token 无效/过期，停止流程 |
| 客户端 | 429 限流 | 退避重试（串行化 + 指数退避），不静默丢弃 |
| 客户端 | `success=false` | 抛 `BusinessError`，**原文展示** message |
| 路由 | 参数不配对（60002） | 拦截并指出是哪个参数配错，不自动"猜" |
| 计价 | 单方案失败 | 标记"计价失败"，继续其他方案，**不隐藏** |
| 闸门 | 未确认/超限 | 拦截并说明原因 |
| 业务 | 门店非营业（600057） | 提示营业时间，建议换门店或预约 |

完整错误码见 `references/error_codes.md`。

---

## 六、目录结构

```
contest-project/                      （仓库根 = Skill 根）
├── SKILL.md                          作品本体
├── README.md                         项目介绍 / 安装 / 示例 / 目标用户
├── MCP_INTEGRATION.md                实际使用的 MCP 能力与链路
├── CONTEST_DECLARATION.md            参赛声明（不可改）
├── mcp-config.example.json           脱敏配置
├── workbuddy.md                      WorkBuddy 开发上下文
├── scripts/
│   ├── mcd_client.py                 ✅ MCP 客户端
│   ├── router.py                     场景路由
│   ├── coupons.py                    券可用性判定
│   ├── guard.py                      写操作闸门
│   ├── planner.py                    ⏳ 候选与比价
│   └── render.py                     ⏳ 输出渲染
├── references/
│   ├── product-spec.md               产品定义
│   ├── design-standards.md           适老设计标准（可验收）
│   ├── architecture.md               本文件
│   ├── output-format.md              输出消息格式规范
│   ├── mcd-mcp-tools-reference.md    ✅ 35 工具调研
│   ├── be_type_matrix.md             取餐方式参数矩阵
│   └── error_codes.md                错误码与处置
└── tests/
    └── test_core.py                  确定性逻辑单测
```

---

## 七、限流与性能

| 风险 | 现状 | 对策 |
|---|---|---|
| 限流 | 600 次/分钟 | 串行化调用 + 缓存门店/菜单（同一次会话内复用） |
| 大 payload | `query-my-coupons` 单次 38KB、`campaign-calendar` 32KB | L2 层统一裁剪，只保留业务字段 |
| 比价放大 | 1 次比价可能 10+ 次 `calculate-price` | 限制候选上限（默认 5 个），并缓存同参数结果 |
