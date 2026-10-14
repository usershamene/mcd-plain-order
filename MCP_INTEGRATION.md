# MCP 接入说明

## 一、使用的 MCP Server

| 项目 | 值 |
|---|---|
| 服务名称 | 麦当劳中国 MCP Server（`mcd-mcp v1.0.0`） |
| 接入地址 | `https://mcp.mcd.cn` |
| 传输协议 | Streamable HTTP |
| 鉴权方式 | `Authorization: Bearer <MCP_TOKEN>`（环境变量注入，不入库） |
| 在线工具数 | **实测 35 个** |

脱敏配置见 [`mcp-config.example.json`](./mcp-config.example.json)，Token 仅以 `${MCD_MCP_TOKEN}` 占位符表示。

### 工具调用的统一入口

全部调用经由 [`scripts/mcd_client.py`](./scripts/mcd_client.py)，它承担三件不能交给模型的事：

1. **鉴权与 `Bearer ` 前缀**（实测缺前缀会返回 403 + code 400003）
2. **业务状态显式校验** —— 麦当劳 MCP 的业务失败**不通过 MCP 错误机制上报**，返回的仍是正常结构、只有 `success=false`；客户端会抛 `BusinessError`，避免把失败当成功
3. **协议错误分类** —— 401 鉴权失败 / 429 限流分别处理

## 二、实际使用的 Tools

### 2.1 已用真实 Token 调用验证过的工具（15 个）

| Tool | 在项目中的作用 | 调用结果 |
|---|---|---|
| `now-time-info` | 取服务器时间，判断早餐时段与营业状态 | ✅ 返回 GMT+8 时间戳 |
| `query-nearby-stores` | 按城市+地点搜门店（到店自取 beType=1 / 得来速 beType=5） | ✅ 5 家门店 / 4 家得来速，含 storeCode 与 beCode |
| `query-meals` | 取门店在售餐品（餐品 code 的唯一来源） | ✅ 11 个分类 + 91 个餐品 |
| `query-meal-detail` | 取套餐组成与可替换项 | ✅ 返回 rounds 分组 |
| `query-my-coupons` | 取卡包券（含日内有效期窗口） | ✅ 13 张，**单次 57KB** |
| `query-store-coupons` | 取**该门店真正可用**的券 | ✅ 仅 1 张（≠ 卡包 13 张） |
| `available-coupons` | 取可领取的麦麦省券 | ✅ 共 6 项，可领 3 |
| `query-my-account` | 取积分账户 | ✅ 可用 180.7 分 |
| `delivery-query-addresses` | 取配送地址（外送链路前置） | ✅ 返回空数组（链路需先建地址） |
| `campaign-calendar` | 取当月活动日历 | ✅ 今日 14 场，**单次 32KB** |
| `mall-points-products` | 取积分商城商品 | ✅ 50 个（shopId=2 积分券 / 5 现金活动） |
| `query-lottery-info` | 取抽奖活动与消耗规则 | ✅ 含 `drawDecision` |
| `query-my-prizes` | 取中奖记录 | ✅ 4 条 |
| `order-list` | 取历史订单（复购画像基础） | ✅ 10 条，含 `realTotalAmount` |
| `list-nutrition-foods` | 取营养成分 | ✅ 160 条（**压缩表格字符串，非 JSON**） |

### 2.2 已在架构中规划、尚未实跑的工具（10 个）

诚实标注：以下工具的参数与流程已按官方 schema 与文档设计，但**本项目尚未用真实 Token 跑通**，属于下一阶段工作：

| Tool | 规划用途 | 未跑通原因 |
|---|---|---|
| `calculate-price` | 多方案真实计价 + 取 `takeWayCode` | 需先有可用门店与餐品组合 |
| `create-order` | 下单 | 写操作，需用户确认 |
| `query-order` | 查订单状态 | 需先有订单 |
| `cancel-order` | 取消订单 | 写操作 |
| `delivery-create-address` | 建配送地址（打通外送链路） | 需用户真实收货信息 |
| `delivery-query-stores` | 按地址查可配送门店 | 依赖上一条 |
| `auto-bind-coupons` | 一键领券 | 写操作 |
| `query-meal-assistance` | 团餐助餐服务 | 属团餐场景 |
| `query-promotions` | 团餐满减规则 | 属团餐场景 |
| `draw-lottery` | 积分抽奖 | 写操作，需用户确认 |

## 三、调用流程

### 3.1 点餐主链路

```
now-time-info                                    判断时段
   ↓
router.choose_take_mode(has_address)             本地决策：到店 or 麦乐送
   ↓
query-nearby-stores / delivery-query-stores      → storeCode (+beCode)
   ↓                                              ← 参数由 router.build_args() 构造
query-meals                                      → 餐品 code
query-my-coupons + query-store-coupons           → 券（含日内窗口判定）
   ↓                                              ← coupons.evaluate() 判定
calculate-price × N                              → 真实计价 + takeWayList
   ↓
guard.check() → 展示三行金额 → 用户确认           ← 本地闸门
   ↓
create-order → guard.receipt_notice()            → 支付链接 + 撤销提示
```

### 3.2 券提醒链路（日内窗口）

```
now-time-info
   ↓
query-my-coupons ─┐
                  ├→ coupons.urgent(coupons, now, beType, threshold_minutes=90)
query-store-coupons┘        ↓
                        输出「现在可用且即将失效」的券，按剩余分钟升序
```

**这条链路的核心发现**：麦当劳券多为**日内时段券**（早餐券仅当天 05:00–10:29 有效），因此提醒的关键指标是**距窗口关闭的分钟数**，而不是「距到期还有几天」。

## 四、业务价值

| 价值 | 说明 | 支撑 |
|---|---|---|
| **降低决策成本** | 用户不用先想清「到店还是外送」，系统按地址自动编排 | `router.choose_take_mode()` |
| **让优惠真正用上** | 13 张卡包券在某门店实际可用仅 1 张；且 11 张不支持外送——不判定就会误导用户 | `coupons.evaluate()` |
| **避免"白扔钱"** | 券是日内窗口，错过当天即作废；分钟级提醒有真实行动价值 | `coupons.urgent()` |
| **价格透明** | 所有价格来自官方计价接口，零估算；金额三行明示 | `calculate-price` + 渲染层 |
| **安全可逆** | 写操作默认拦截，确认后才执行；回执主动给出撤销路径 | `guard.py` |
| **失败不掩盖** | 业务失败一律显性抛出，不做「看起来成功」的输出 | `mcd_client.py` |

## 五、已知限制

1. **外送链路未打通**：`delivery-query-addresses` 实测返回空数组，需先创建配送地址（写操作，需用户真实信息）
2. **门店营业时段限制**：非营业时间查菜单返回 `600057`
3. **限流**：600 次/分钟；一次多方案比价可能触发 10+ 次计价调用，需串行化与缓存
4. **大 payload**：`query-my-coupons` 57KB、`campaign-calendar` 32KB，必须裁剪字段后再交给模型
5. **数据质量**：`campaign-calendar` 顶层字段（activityTitle/price）大量为空，真实文案在 `articleDto` 内
6. **本项目不存储任何用户数据**，Token 仅从环境变量读取
