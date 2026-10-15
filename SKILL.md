---  
name: mcd-plain-order  
description: 麦麦明白点（mcd-plain-order）—— 以「极速点单」为尖入口的对话式麦当劳点餐助手：一句话自动判断取餐方式（到店自取 / 得来速 / 麦乐送 / 企业团餐），真实计价对比、预约到店即取，覆盖选餐、算价、下单到售后全流程，并按用户偏好个性化（避开不喜欢的、主动告知喜欢的新品/券）。只要用户提到点麦当劳、想吃某样单品、问附近门店或菜品/套餐内容、问有什么券或怎么点最省、要对比几套方案、查或取消订单、或替家人点餐，都应使用本技能——即使没说「点餐」二字。所有输出金额三行明示、说人话、无诱导，写操作 10 分钟内可撤销。  
agent_created: true  
---

# 麦麦明白点（mcd-plain-order）

## 用途

把「点一份麦当劳」这件事，从「在 App 里翻菜单、比价格、凑满减」变成「说一句话，明明白白点好」。

**尖入口 = 极速点单**：面向通勤 / 赶时间人群，一句话完成「选店 + 选品 + 预约 + 到店即取」，全链路以时间为最优（对标 mcd-mcdrive-mate，但覆盖更全、且含个性化与履约兜底）。

面向全体消费者（早餐与通勤人群为主），并把**工信部适老化设计规范**作为硬标准落地，因此它也照顾到不熟悉多步操作的用户。

**与官方 App 的差异**：官方 App 已经能点餐。本 Skill 的价值在四件事——**编排**（自动判断取餐方式）、**对比**（多方案真实计价对比，而不是只给一个）、**明确**（金额永远三行，不藏着）、**可逆**（写操作明示 10 分钟内可撤销）。

## 前置条件

1. 已配置 mcd-mcp 连接器（配置见仓库 `mcp-config.example.json`），Token 通过环境变量 `MCD_MCP_TOKEN` 注入
2. 读取会员数据（积分/券/订单）要求 Token 已绑定麦当劳会员账号
3. Python 3.10+，仅标准库，无需安装第三方包

## 工作流程

### 主链路（点餐）

```
1. now-time-info                       → 当前时间（判断早餐时段/营业状态）
2. 路由：router.choose_take_mode()      → 决定取餐方式（有地址→麦乐送，否则到店自取）
   └─ 得来速/团餐需用户明确指定
3. query-nearby-stores / delivery-query-stores → storeCode（+ beCode）
   └─ 参数用 router.build_args() 构造，禁止手写
4. query-meals                         → 餐品 code（唯一来源）
5. query-store-coupons + query-my-coupons
   └─ coupons.evaluate() 判定可用性（含日内窗口）
6. calculate-price × N                 → 真实计价 + takeWayList[].code
7. guard.check() 闸门 → 展示三行金额 → 等用户确认
8. create-order                        → 支付链接
9. 输出回执（含撤销提示，用 guard.receipt_notice()）
```

### 极速点单专用路径（尖入口）

面向赶时间的用户，把"选店 + 算预约 + 下单"压缩成一屏答案。确定性决策全部在代码里（不依赖模型）：

```
1. choose_pickup_method(context)      → 选方式：用户指定 > 开车(得来速) > 有地址(麦乐送) > 到店自取
2. select_nearest_store(stores)        → 仅营业中且支持预约，按 distance 升序（得来速模式只在得来速店中选）
3. estimate_eta_minutes(distance,mode) → 按步行 70 / 驾车 250 米每分钟估 ETA（至少 1 分钟）
4. pick_reservation_window(now,eta,opts) → 算"到店即取"时间 = now+eta+缓冲5分，落在门店预约时段内；
                                          落在间隙则顺延到下一时段起点；晚于全部时段则当天不可约
5. render.render_fast_plan(plan)       → 输出「🍔 极速点单（方式 · 预计N分钟）」+ 门店 + 预约到店
6. 用户确认后：calculate-price → guard → create-order（带 reservationDate）
```

麦乐送不挂门店预约时段（标题已给"预计 N 分钟送达"），仅展示派单门店。

### 关键顺序约束（不可跳层）

- `beCode` 只能在第 3 步拿到
- `takeWayCode` **只能**来自第 6 步 `calculate-price` 的 `takeWayList[].code`
- `orderId` 只能来自工具返回或用户输入，**禁止生成**

## 强制规则（全部来自实测踩坑）

### 1. 必须显式校验业务返回

麦当劳 MCP **不通过 MCP 错误机制上报业务失败**：失败时返回的仍是正常结构，只是 `success` 为 `false`。

```
success == true 且 code == 200  →  业务成功
success == false               →  业务失败，原文展示 message，禁止伪造成功
```

统一用 `scripts/mcd_client.py` 调用，它会在业务失败时抛 `BusinessError`。

### 2. 参数必须经 router 构造，禁止手写

| 取餐方式 | beType | orderType | beCode                      |
| ---- | ------ | --------- | --------------------------- |
| 到店自取 | 1      | 1         | **不传**（传了报错）                |
| 得来速  | 5      | 1         | 必传，来自 query-nearby-stores   |
| 麦乐送  | 2      | 2         | 必传，来自 delivery-query-stores |
| 企业团餐 | 6      | 2         | 必传 + `gmServiceCode`        |

配对错误 → `code=60002`。完整矩阵见 `references/be_type_matrix.md`。

### 3. 券的判定必须用代码

券是**日内时段券**（实测：早餐券仅当天 05:00–10:29 有效），所以核心指标是「距窗口关闭还有几分钟」，不是「还有几天到期」。

用 `scripts/coupons.py` 判定，注意两件事：

- **卡包券 ≠ 门店可用券**（实测：卡包 13 张，某门店实际可用仅 1 张）
- **渠道限制**：`onlyAppCanRedeem` / `onlyPosCanRedeem` 为真时，用户在对话里无法核销，只能提醒不能代办

### 4. 写操作必须过闸门

`create-order`、`cancel-order`、`mall-create-order`、`party-order-create`、`auto-bind-coupons`、`draw-lottery`、`delivery-create-address` 全部经 `scripts/guard.py`，**默认未确认即拦截**。

`draw-lottery` 额外要求：先展示 `query-lottery-info` 的 `drawDecision.nextConsumption`，确认后才可抽；严禁试抽、连抽。

### 5. 输出必须符合设计标准

- 金额**永远三行**：原价 / 优惠 / 实付
- **不用专业词**：不说「随心配/核销/券包」，说「优惠套餐/用了/优惠券」
- **无诱导**：不出现「限时」「仅剩」；凑单建议只在用户主动询问时给
- **无默认勾选**：餐具、加购、备注一律默认关闭
- 下单回执必须含撤销路径与时限

完整清单：`references/design-standards.md`

## 个性化（用户画像）

基于用户的历史偏好与时间特征做个性化，但**状态与规则都在 Skill 内**，不依赖外部 Agent/AI 服务——Skill 本就运行在 Agent 内部，无需另起组件。

- **持久化**：`scripts/personalize.py` 读写 `user_profile.json`（Skill 本地文件）。MCP 不存自由文本的用户备注，画像由本 Skill 自己维护。
- **提取（模型）**：当用户在对话中表达稳定偏好（如"我不吃辣""我喜欢冰淇淋"），先向用户确认一句"我记住你不喜欢辣了，可以吗？"再调用 `personalize.merge_preference()` 写入；同一偏好自动去重。
- **应用（代码，确定性）**：每次生成推荐前先 `personalize.load_profile()`，再用 `filter_recommendations()` 过滤不喜欢的、`matches_like()` / `liked_relevant()` 高亮喜欢的。
- **时间个性化**：用 `personalize.time_context()`（或 MCP `now-time-info`）给出餐段（早餐/午市/晚市）与周末标记，早餐时段优先推早餐、非营业时段给出提示。
- **主动告知（非推销）**：仅当用户**明确说过喜欢**某类（如冰淇淋），且周边出现该类**新品**（数据源 `scripts/newitems.py`，渲染加"· 你喜欢"）或**相关优惠券**（数据源 `scripts/reminders.build_coupon_reminder(profile=...)`，命中喜欢品类的券渲染加"· 你喜欢"）时，才主动提及。这与"不做主动促销"不冲突——它是兴趣匹配，不是广撒网。

只存提取后的结构化偏好，**不存原始对话历史**（隐私更可控、规则更确定）。

## 可用脚本

| 脚本                       | 用途                                | 何时用                                                            |
| ------------------------ | --------------------------------- | -------------------------------------------------------------- |
| `scripts/mcd_client.py`  | MCP 客户端（鉴权/会话/业务校验）               | 所有工具调用                                                         |
| `scripts/router.py`      | 取餐方式路由与参数构造                       | 每次构造工具参数前                                                      |
| `scripts/coupons.py`     | 券可用性判定与紧迫度排序                      | 涉及券的任何场景                                                       |
| `scripts/guard.py`       | 写操作闸门与回执话术                        | 任何写操作前                                                         |
| `scripts/personalize.py` | 用户画像读写、偏好筛选/高亮、时间餐段               | 每次推荐前 + 提取偏好时                                                  |
| `scripts/render.py`      | 标准化输出渲染（四段式 + 三行金额，确定性模板）         | 任何面向用户的消息                                                      |
| `scripts/newitems.py`    | 新品发现（从菜单筛"上新"类 tag）+ 个性化标记        | 生成 🆕 新品段前                                                     |
| `scripts/reminders.py`   | 积分目标计算（可换/还差）+ 券紧迫度筛选             | 生成 ⭐/🎫 段前                                                     |
| `scripts/fast_order.py`  | 极速点单编排：选方式/选最近店/算 ETA/预约到店（确定性决策） | 极速点单尖入口；`render.render_fast_plan()` 渲染方案                       |
| `scripts/workflow.py`    | 极速点单端到端编排：选店→算价→下单，确定性逻辑 + 网络委托   | 串起整条链路；client 可注入（线上 `mcd_client.MCDClient` / 测试 `FakeClient`） |

```bash
python scripts/mcd_client.py list-tools
python scripts/mcd_client.py call query-my-account
python examples/fast_order_demo.py            # 极速点单形态演示（真实门店数据）
python examples/online_demo.py                # Token 就绪演示：设了 MCD_MCP_TOKEN 走真实只读预览，否则 FakeClient 离线回放
python examples/smoke_live.py                 # 真·只读联调（需 MCD_MCP_TOKEN）：抓真实响应校准字段
python -m unittest discover -s tests           # 126 个单测，验证全部确定性逻辑
```

### 线上联调（无 Token 也能跑，且防误操作）

`workflow.run_fast_order(client, context, items, now, confirmed, stores=...)` 把「选店 → 算价 → 下单」串起来，  
所有确定性决策（路由 / 方案 / 金额映射 / 字段提取）写死在代码里，**网络调用全部委托给注入的 client**。

- **测试 / 演示**：用 `tests/test_workflow.py` 的 `FakeClient`（回放真实探针 05 + 模拟计价/下单响应），  
  不依赖真实 Token 即可确定性跑通，专门验证 beCode 配对、takeWayCode 来源、reservationDate、orderId 不生成等真实风险点。
- **接真实麦当劳 MCP**：`examples/online_demo.py` 已做自动切换——检测到 `MCD_MCP_TOKEN` 即用 `scripts/mcd_client.MCDClient`，  
  否则回退 `FakeClient`。调用契约一致（都是 `.call(name, args)`），编排层无需改动。
- **两阶段**：`confirmed=False` 只出「方案 + 算价预览」（不写）；`confirmed=True` 才过 `guard` 闸门下单。
- **只读安全约定（重要）**：live 模式默认只跑 `confirmed=False` 预览（仅 `query-nearby-stores` / `calculate-price` 等只读调用），**绝不自动下单**；  
  真下单必须显式加 `--really-order`，且脚本只对「到店自取」单一场景下单，避免把演示脚本当成批量下单工具造成多笔真实订单。

### 真·联调校准结论（已用真实 Token 验证）

`examples/smoke_live.py` 用真实 Token 跑通「到店自取」全链路只读预览，实测修正了以下字段/约束（原为基于文档的假设）：

| 项                     | 实测结论                                                                                           | 处理                                                                               |
| --------------------- | ---------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| 商品字段                  | **`productCode` + `quantity`**（不是 `code`/`qty`）——传错会 600010 或静默空计价                             | `router.build_args` 在边界统一转换，内部仍用易读的 `code`/`qty`                                 |
| 金额单位                  | **分**（`originalPrice:1950` = ¥19.5，`price:1450` = ¥14.5）                                       | `workflow._extract_price` 统一 `/100` 换算成元                                         |
| 优惠字段                  | 顶层 `discount`（分）；无优惠时为 `0`                                                                     | 渲染时 `discount<=0` 不显示「优惠」行                                                       |
| takeWayCode           | `takeWayList[].code`（如 `eat-in`）                                                               | `workflow._extract_take_way_code`，外送不传                                           |
| `calculate-price` 入参  | **不传 `reservationDate`**（那是 create-order 的参数，传了会让部分商品 60006）；beType=5/6 时**必传 beCode**（600048） | 计价只传 items/storeCode/beType/orderType（+beCode）；`reservationDate` 仅进 create-order |
| `query-nearby-stores` | searchType=2 时 **city 与 keyword 都必填**（否则 600058）；**同一入参两次调用可能返回不同门店**                          | 缺 keyword 回退城市名；`run_fast_order(..., stores=...)` 支持注入，保证「选品门店 == 计价门店」          |
| DT 门店                 | beType=5 的 `query-nearby-stores` 会返回 `beCode`                                                  | `_extract_be_code` 取自响应，缺失即显式报错                                                  |

> 真联调现状：到店自取（只读预览）+ 麦乐送、团餐（均已真实下单，验证全链路）已用真实 Token 跑通；得来速（dt, beType=5）仍仅离线单测覆盖，本次补了一次真实只读预览验证 beCode 配对（见 `examples/smoke_live.py` 第 6 步）。create-order 属写操作，未经用户显式确认不自动执行。

### 企业团餐（group）链路已落地

`workflow.run_fast_order` 现已支持第四种取餐方式 `group`（beType=6 / orderType=2），离线单测已覆盖：

```
delivery-query-stores(addressId, beType=6) → storeCode + beCode
  ↓
query-meal-assistance(storeCode, beType=6, orderType=2, beCode) → gmServiceCode   （须在 calculate-price 之前）
  ↓
calculate-price(..., beCode, gmServiceCode) → 金额
  ↓
create-order(..., beCode, gmServiceCode, addressId)
```

- `gmServiceCode` **只能**来自 `query-meal-assistance`（优先取 `selected=True` 项，否则第一个 `enable=True`），缺失即失败显性化，与 `takeWayCode` 一样严禁自造。
- 团餐与麦乐送同为 orderType=2，**不传** `takeWayCode` / `reservationDate`（渲染按"送达"处理）。
- 真实团餐联调同样需真实 `addressId`（写操作 `delivery-query-addresses` 取得），live 演示默认跳过，避免误建地址。

## 参考文件

| 文件                                      | 内容                      |
| --------------------------------------- | ----------------------- |
| `references/product-spec.md`                  | 产品定义、功能矩阵、非目标           |
| `references/design-standards.md`              | 适老设计标准（可验收清单）           |
| `references/architecture.md`                  | 六层架构、模块契约、错误策略          |
| `references/output-format.md`                 | 输出消息格式规范（分区/区分/最少字最多信息） |
| `references/be_type_matrix.md`          | 四种取餐方式的完整参数矩阵           |
| `references/error_codes.md`             | 错误码、易误判场景、参数格式          |
| `references/mcd-mcp-tools-reference.md` | 35 个 MCP 工具完整调研         |

何时读：构造取餐 / 计价 / 下单参数前查 `be_type_matrix`；遇到 `6000x` 报错查 `error_codes`；需要某工具的字段或行为查 `mcd-mcp-tools-reference`；生成任何面向用户的消息前查 `output-format` 与 `design-standards`；需要产品定位 / 边界或模块契约时查 `product-spec` / `architecture`。

## 边界与限制

- **不做**：库存/排班/销量（MCP 无此数据）、排队预测、与其他品牌对比、占座类场景
- 门店非营业时间查菜单会返回 `600057`，需提示营业时间或改用预约
- `list-nutrition-foods` 返回的是**压缩表格字符串**，非 JSON，需自行解析
- 营养餐品名与菜单 code 无直接映射，做联动需维护映射表
- 外送链路要求用户有已保存的配送地址；无地址时需先创建（写操作，需确认）
- 调用限流 600 次/分钟；`query-my-coupons` 单次可达 38KB，必须裁剪字段
- 本 Skill 输出仅供参考，不构成营养或医疗建议
