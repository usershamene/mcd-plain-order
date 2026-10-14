# 麦当劳中国 MCP Server 工具深度调研报告

> 调研时间：2026-10-10 23:50（北京时间）
> 数据来源：直接对 `https://mcp.mcd.cn` 发起 MCP 协议调用（`initialize` + `tools/list` 取全量真实 schema，再以 `tools/call` 实测只读工具的真实返回）
> 服务版本：`mcd-mcp v1.0.0`，实测在线工具 **35 个**（官方 README 宣传约 40 个，以实测为准）
> 原始 schema 存档：`mcp-research/tools_schema.json`

---

## 一、总览：35 个工具的功能分区

| 分区 | 数量 | 工具 |
|---|---|---|
| 基础 | 1 | now-time-info |
| 门店与地址 | 4 | query-nearby-stores、delivery-query-stores、delivery-query-addresses、delivery-create-address |
| 点餐主链路 | 7 | query-meals、query-meal-detail、calculate-price、create-order、query-order、order-list、cancel-order |
| 优惠券 | 5 | available-coupons、auto-bind-coupons、query-my-coupons、query-store-coupons、query-survey-coupon |
| 积分与商城 | 6 | query-my-account、mall-points-products、mall-product-detail、mall-create-order、mall-order-list、mall-order-detail |
| 积分抽奖 | 3 | query-lottery-info、draw-lottery、query-my-prizes |
| 活动与派对 | 6 | campaign-calendar、query-party-city、query-party-store、query-party-store-date、query-party-store-session、party-order-create |
| 企业团餐专项 | 2 | query-meal-assistance、query-promotions |
| 营养数据 | 1 | list-nutrition-foods |

---

## 二、贯穿所有工具的三条底层规则（最容易踩坑，先看这里）

### 规则 1：beType / orderType / beCode 三件套配对表

这是所有点餐类工具共用的参数约定，**配错直接报错**（实测错误码 60002：「未查询到门店信息, 检查 storeCode 和 beCode 是否配对」）。

| 点餐方式 | beType | orderType | beCode | beCode 来源 |
|---|---|---|---|---|
| 到店自取 | 1 | 1 | **不传**（传了报错） | — |
| 得来速 DT | 5 | 1 | 必传 | query-nearby-stores |
| 麦乐送 | 2 | 2 | 必传 | delivery-query-stores |
| 企业团餐 | 6 | 2 | 必传 | delivery-query-stores |

### 规则 2：统一响应信封（实测结果，与声明的 outputSchema 不一致）

工具的 `outputSchema` 声明为 `{data, message, traceId}`，但**实测真实返回是统一信封**：

```json
{
  "success": true,
  "code": 200,
  "message": "请求成功",
  "datetime": "2026-10-10 23:50:34",
  "traceId": "f06d5431e3fc323cc190ec3060cf7d3b",
  "data": { ... }
}
```

### 规则 3：业务错误不通过 MCP 的 isError 上报

**这是最重要的发现。** 参数错误、门店不匹配、无库存等业务失败，返回的是 `isError` 为假、但 `success: false` 的正常结果。调用方不能只判断「有没有报错」，**必须显式检查 `success` 字段和 `code`**，否则会把失败当成功。

实测遇到的错误码：

| code | message | 触发场景 |
|---|---|---|
| 60002 | 未查询到门店信息, 检查 storeCode 和 beCode 是否配对 | 门店编码错误或 beCode 配错 |
| 600050 | 收藏餐厅列表为空，请您提供城市+关键词进行搜索 | searchType=1 但用户无收藏门店 |
| 600057 | 门店可能已关闭或不在营业时间 | 非营业时段查菜单（实测 23:50 查询失败） |
| 400003 | 校验鉴权authToken必填 | Token 缺少 `Bearer ` 前缀 |
| 401 / 429 | Token 无效 / 超 600 次每分钟 | 见第六节 |

---

## 三、逐工具详解

### 3.1 基础类

#### now-time-info
- **功能**：返回服务器当前完整时间（毫秒时间戳、格式化日期、年月日、时区、UTC）。
- **参数**：无。
- **返回**：`data.{timestamp, datetime, formatted, date, year, month, day, dayOfWeek, timezone, offset, utc}`
- **场景**：所有涉及「今天/这周/本月」的时间判断起点；查活动日历前先取当前时间。
- **限制**：返回的是**服务器时间**（GMT+8），不要用模型自己推算当前时间。

### 3.2 门店与地址类

#### query-nearby-stores（到店场景）
- **功能**：查到店自取 / 得来速门店。
- **参数**：`beType`(必填, 1或5)、`searchType`(必填, 1=搜收藏餐厅，2=按位置搜)、`city`(searchType=2 必填)、`keyword`(searchType=2 必填)
- **返回**：`data` 门店列表，含 storeCode；**beType=5 时返回 beCode**，beType=1 时不返回。
- **场景**：用户说「我想到店」「附近有什么麦当劳」「我要开车去」。
- **限制**：实测 searchType=1 且无收藏时返回 600050 错误，需回退到 searchType=2 并索要城市+关键词。

#### delivery-query-stores（外送场景）
- **功能**：按配送地址查可配送门店。
- **参数**：`addressId`(必填)、`beType`(必填, 2=麦乐送 / 6=团餐)
- **返回**：门店列表，含 storeCode + beCode。
- **前置**：addressId 必须先由 delivery-query-addresses 获取。
- **限制**：到店场景用错会失败，官方明确「到店请用 query-nearby-stores」。

#### delivery-query-addresses
- **功能**：查用户已保存的配送地址列表。
- **参数**：无。
- **返回**：实测无地址时为 `data.addresses: []`。
- **场景**：点麦乐送、点团餐的第一步。
- **限制**：实测该 Token 绑定账号下地址为空，需先创建。

#### delivery-create-address
- **功能**：新增配送地址。
- **参数**：`city`、`contactName`、`phone`(11位纯数字)、`address`(如"清竹园9号楼")、`addressDetail`(门牌号) 均必填；`gender` 可选。
- **返回**：创建结果。
- **限制**：所有字段必须来自用户真实输入；电话号码必须 11 位纯数字且以 1 开头。**属写操作**，调用前须与用户确认。

### 3.3 点餐主链路（7 个）

| 工具 | 功能 | 关键参数 | 返回要点 | 限制 |
|---|---|---|---|---|
| **query-meals** | 查门店可售餐品菜单（含分类、餐品编码、标签、价格） | storeCode 必填；beType、orderType；预约时传 reservationDate | 餐品列表，是**获取商品 code 的唯一来源** | 实测非营业时间报 600057；套餐搭配需先取 code |
| **query-meal-detail** | 查餐品详情/套餐组成（用于换配） | `code` + storeCode + beType + orderType | 实测返回 `rounds[]`：每个 round 含可选 choices（code/name/isDefault/diffPrice/supportModify），支持改特制 | 换配仅在 1.0.5+ 支持；所得 choice.code 用于组装 items |
| **calculate-price** | 算价（含优惠券、配送费），返回应付总额 | `items`(选购列表)、`storeCode`、`beType`、`orderType`；团餐需 `gmServiceCode`；`needTableware`；`withOrder`(随单购) | 返回金额明细 + **takeWayList[].code**（到店/得来速下单必用） | 下单前必须算价，takeWayCode 只能从这里拿 |
| **create-order** | 创建订单，返回支付链接 | `storeCode`、`beType`、`orderType`、`items` 必填；外送需 `addressId`；到店/得来速需 `takeWayCode`；`remark`(≤50字, 仅外送)；预约需 `reservationDate`(格式 `yyyy-MM-dd HH:mm`) | 订单详情 + 支付链接 | **写操作**；到店场景传 beCode 会报错；外送场景不传 takeWayCode |
| **query-order** | 查单个订单详情/进度 | `orderId` 必填 | 订单状态、内容、配送信息 | orderId 必须由上游工具或用户提供，**禁止模型自己编造** |
| **order-list** | 查历史点餐订单（非商城） | 无 | 实测返回 `data.list[]`，含 orderId、orderType、storeCode、storeName、orderStatus、orderProductList | 商城订单要用 mall-order-list |
| **cancel-order** | 取消订单 | `orderId`、`cancelReasonCode` 必填 | 取消结果 | 原因码：1改主意/2重复下单/3点错/4地址电话错/5时间错/-1其它。**写操作** |

> **⚠️ 实测校准修正（2026-10-13，用真实 Token 跑通「到店自取」只读链路得到）**：
> - `calculate-price` / `create-order` 的 **items 每项字段是 `productCode` + `quantity`**（不是 `code`/`qty`）。传 `qty` 报 600010；传 `code` 会静默空计价（productList=[]，金额为 0）。
> - 返回金额单位是**分**（`originalPrice:1950` = ¥19.5，`price:1450` = ¥14.5，`discount` 同理）。顶层优先字段是 `originalPrice` / `price`（不是 `payPrice`）。
> - `calculate-price` 入参**不要传 `reservationDate`**（那是 create-order 的参数；传了会让部分商品报 60006 售罄/不可售）；但 **beType=5/6 时必传 `beCode`**（否则 600048）。
> - `query-nearby-stores`：`searchType=2` 时 `city` **与** `keyword` 都必须非空（否则 600058）；且**同一入参两次调用可能返回不同门店**，选品与计价之间要复用同一份 stores。

### 3.4 优惠券类（5 个）

| 工具 | 功能 | 参数 | 返回 / 限制 |
|---|---|---|---|
| **available-coupons** | 麦麦省「可领取」券列表 | 无 | 实测返回券名、图片、couponStatus(如 HAVING_RECEIVE)、label("已领取") |
| **auto-bind-coupons** | 一键领取所有可领券 | 无 | **写操作**；无需指定 couponId |
| **query-my-coupons** | 我的卡包券资产 | `page`(默认1, 最多5页)、`pageSize`(默认200, 最大200) | 实测单次返回 **38,622 字符**，payload 极大；**不做门店/渠道校验**，不代表当前订单可用 |
| **query-store-coupons** | 指定门店+订单类型下**真正可用**的券 | storeCode(纯数字)、beType、orderType、reservationDate | 下单前判断用券的**正确工具**；需 storeCode 已确定 |
| **query-survey-coupon** | 按订单号查 CSAT 满意度答卷及关联奖券 | `orderId` 必填 | 券标题、核销时间、核销状态、点餐方式；原始答卷/会员号不可猜测 |

> **关键区分**：`query-my-coupons` = 我有什么券（不校验）；`query-store-coupons` = 这家店这单能用什么券（校验过）。做「省钱」类功能必须用后者。

### 3.5 积分与商城（6 个）

| 工具 | 功能 | 参数 | 返回要点 |
|---|---|---|---|
| **query-my-account** | 积分账户 | 无 | 实测：`availablePoint`(180.7)、`accumulativePoint`、`frozenPoint`、`currentMouthExpirePoint`、`nextMouthExpirePoint`、`expiredPoint`、`usedPoint`、`currency`("麦享会积分")、`accountId` |
| **mall-points-products** | 积分商城商品/活动列表 | `catRuleIds` 可选，可多选逗号分隔 | 类目码：`1>4`商品券、`2`实物、`2>8`周边、`2>9`礼品卡、`1>6>20`生日派对、`1>6>21`主题派对、`1>6>22`麦麦体验营、`1>6>25`品鉴会、`1>6>34`读书会、`1>6>40`积分兑换活动。实测返回含 spuId/spuName/point/shopId/status |
| **mall-product-detail** | 商品详情（SKU 规格） | `spuId` 必填 | 用户选 SKU 后才能下单；**shopId 决定用哪个下单工具**（5→party-order-create，2→mall-create-order） |
| **mall-create-order** | 积分兑换下单（仅 shopId=2） | `skuId`、`spuCategory`("1"虚拟券/"2"实物）必填；实物需 `addressId` | 多商品**必须分别下单且串行排队**，等前一单响应成功再下一单 |
| **mall-order-list** | 商城订单列表 | `lastId`、`size`(默认10,超10按10) | 近一年商城订单 |
| **mall-order-detail** | 商城订单详情 | `orderId` 必填 | 支付积分、金额、状态 |

### 3.6 积分抽奖（3 个）

| 工具 | 功能 | 参数 | 关键约束 |
|---|---|---|---|
| **query-lottery-info** | 查抽奖活动信息（不抽奖） | 无 | 实测返回活动名「周六麦金喜抽奖」、`drawPoint`(24)、`drawTypeText`(消耗积分抽奖)、`availablePoint`、`drawDecision.{resourceEligible,nextConsumption,fallbackConsumption}` |
| **draw-lottery** | 执行一次抽奖 | 无 | **强约束**：必须先展示 query-lottery-info 的 `drawDecision.nextConsumption` 并获用户明确确认；以 `resourceEligible` 判断资格，**不得**用 availableTimes 是否为 0 自行判断；严禁试抽/连抽；有频次限制 |
| **query-my-prizes** | 查我的中奖记录 | `pageNum`(默认1)、`pageSize`(默认10,最大50) | 实测返回 prizes[]：name、prizeType/prizeTypeText、recordTime、status/statusText("可用") |

### 3.7 活动与派对（6 个）——**官方最新能力，竞争最少的区域**

| 工具 | 功能 | 参数 | 依赖 |
|---|---|---|---|
| **campaign-calendar** | 当月营销活动日历 | `specifiedDate`(可选, `yyyy-MM-dd`) | 不传=整月；传了=以该日为锚点，返回前后各一个有活动的日子（共3天）。实测单次返回 **32,405 字符**，payload 大 |
| **query-party-city** | 派对活动可参与城市 | `spuId` 必填 | 前置：mall-points-products → spuId |
| **query-party-store** | 城市下可办派对的**门店** | `code`(城市code)、`spuId`；`latitude`/`longitude` 从 query-party-city 取 | 前置：query-party-city |
| **query-party-store-date** | 门店**可预约日期** | `spuId`、`storeCode` | 前置：query-party-store |
| **query-party-store-session** | 指定日期的**场次** | `spuId`、`storeCode`、`dateStr` | 前置：query-party-store-date |
| **party-order-create** | 派对/体验营/品鉴会下单（仅 shopId=5） | `partyType`(必填, 1包场/2拼团) + spuId/skuId/storeCode/dateStr/场次信息 | **每一步必须等用户明确选择，不得跳步** |

> 支持的活动类型：生日类派对、主题类派对、麦麦体验营、品鉴会、读书会、积分兑换活动。

### 3.8 企业团餐专项（2 个，仅 beType=6 可用）

| 工具 | 功能 | 参数 | 限制 |
|---|---|---|---|
| **query-meal-assistance** | 查门店可用助餐服务 | storeCode + beType=6 + orderType=2 + beCode | 必须在 calculate-price **之前**调用；返回的 gmServiceCode 要传给 calculate-price / create-order |
| **query-promotions** | 查门店团餐促销规则（满减/满折） | storeCode、beCode、beType=6、orderType=2 | 仅返回当前时间有效且适用在售餐品的规则原始数据，**需自行解析** |

> query-meals 的 description 里还内置了团餐预算搭配规则：<20元→小食；20-30→汉堡+小食或饮料；30-40→汉堡+薯条/小食+饮料；40-50→汉堡+薯条+小食+饮料；50+→丰富组合（优先不重复小食）。

### 3.9 营养数据（1 个）

#### list-nutrition-foods
- **功能**：常见餐品营养成分（能量、蛋白质、脂肪、碳水、钠、钙）。
- **参数**：无。
- **返回**：**注意——不是 JSON！** 实测返回的是一个压缩表格字符串：
  ```
  「160」{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:
    猪柳麦满分,null,1288,308,16,16,24,781,213
    猪柳蛋麦满分,null,1618,387,23,21,25,846,243
    ...
  ```
  共 160 条。字段顺序为「品名, 描述, 千焦, 千卡, 蛋白, 脂肪, 碳水, 钠, 钙」。
- **场景**：热量咨询、按目标热量配餐。
- **限制**：需自行解析字符串；品名与 query-meals 的 code 并非一一对应，**做「营养+下单」联动时必须自己做名称映射**，这是该项目最大的实现难点。

---

## 四、工具之间的依赖关系

```
【到店/外送点餐全链路】
now-time-info
  └─ query-nearby-stores(到店/得来速) ── 或 ── delivery-query-addresses → delivery-query-stores(外送/团餐)
        └─ query-meals ──→ query-meal-detail(改配)
              └─ query-store-coupons(查可用券)
                    └─ calculate-price ──(takeWayList[].code)──→ create-order
                          └─ query-order → (可选) cancel-order
                order-list(历史订单，独立)

【优惠券链路】
available-coupons → auto-bind-coupons → query-my-coupons / query-store-coupons → calculate-price(items 带券)

【积分商城链路】
query-my-account → mall-points-products → mall-product-detail ─┬─ shopId=2 → mall-create-order → mall-order-list / mall-order-detail
                                                               └─ shopId=5 → party-order-create

【派对活动链路（严格串行）】
campaign-calendar(发现活动)
mall-points-products(catRuleIds=1>6>xx) → mall-product-detail → query-party-city → query-party-store → query-party-store-date → query-party-store-session → party-order-create

【抽奖链路（严格先查后抽）】
query-lottery-info → (用户确认) → draw-lottery → query-my-prizes

【团餐链路】
delivery-query-addresses → delivery-query-stores(beType=6) → query-meal-assistance / query-promotions → query-meals → calculate-price(gmServiceCode) → create-order

【营养链路】
list-nutrition-foods + query-meals + calculate-price
```

**核心枢纽**：`query-meals`（一切点餐的商品 code 来源）、`calculate-price`（价格与 takeWayCode 双来源）、`mall-product-detail`（shopId 决定下单分支）。

---

## 五、四个典型组合用法（可直接作为 Skill 场景）

### 场景 A：一句话点餐（到店自取）
`now-time-info` → `query-nearby-stores(beType=1, searchType=2)` → `query-meals` → `query-store-coupons` → `calculate-price`(得 takeWayCode) → `create-order` → `query-order` 查支付状态

### 场景 B：外卖省钱最优解
`delivery-query-addresses` → `delivery-query-stores` → `query-store-coupons` + `available-coupons` → `auto-bind-coupons`(可选) → `query-meals` → 多套组合分别 `calculate-price` 比价 → `create-order`(附 remark 无接触配送)

### 场景 C：积分不浪费（推荐参赛方向）
`query-my-account`(看 currentMouthExpirePoint 即将过期) → `mall-points-products(catRuleIds=1>4,2,2>8,2>9)` → `mall-product-detail` → 按过期时间+积分单价排序 → `mall-create-order`（串行下单）

### 场景 D：生日派对预约（推荐参赛方向）
`campaign-calendar` / `mall-points-products(1>6>20)` → `mall-product-detail` → `query-party-city` → `query-party-store` → `query-party-store-date` → `query-party-store-session` → 用户确认 → `party-order-create`

### 场景 E：抽奖提醒
`query-lottery-info`(读 drawDecision) → `query-my-account` 确认积分充足 → 用户明确同意 → `draw-lottery` → `query-my-prizes`

---

## 六、注意事项与限制（做项目必须处理）

### 6.1 协议与鉴权
1. **`Authorization` 必须是 `Bearer <Token>`**，漏掉 `Bearer ` 前缀会返回 403 + code 400003（本次已实测踩坑）。
2. **限流 600 次/分钟**，超限返回 429，需做退避（指数退避或串行化）。
3. Token 401 表示无效/过期/未提供。
4. **Token 直接绑定用户会员账号**——实测该 Token 能读到真实积分余额、卡包券、历史订单。因此：**绝不能把 Token 写进公开仓库**（参赛声明也明确要求配置文件仅用环境变量占位符）。

### 6.2 必须显式处理的业务语义
5. **业务失败不走 isError**，必须判断 `success` / `code`（见第二节规则 3）。
6. **beCode/orderType/beType 三者强耦合**，错一个就 60002。
7. **到店场景绝不能传 beCode**（官方注明「传了会报错」）。
8. **takeWayCode 只能来自 calculate-price**，不能自己构造。
9. **orderId 必须来自上游工具或用户**，禁止模型生成（工具描述里明确写了「Agent 不要自行生成」）。
10. **预约时间格式固定 `yyyy-MM-dd HH:mm`**，非预约场景不要传 reservationDate。

### 6.3 写操作的安全约束
11. `create-order` / `mall-create-order` / `party-order-create` / `cancel-order` / `auto-bind-coupons` / `draw-lottery` / `delivery-create-address` **全部是写操作**，必须二次确认。
12. `draw-lottery` 有强制流程：先展示 `drawDecision.nextConsumption`，等用户确认；**严禁试抽/连抽**；不能用 availableTimes=0 自行判断。
13. `mall-create-order` 多商品必须**串行排队**，等前一单返回成功再发下一单。
14. `party-order-create` **不得跳步**，每一步等用户明确选择。

### 6.4 性能与上下文压力
15. `query-my-coupons` 实测 38,622 字符、`campaign-calendar` 32,405 字符、`list-nutrition-foods` 6,319 字符。**单次调用就可能吃掉大量上下文**，建议在 Skill 里做客户端侧字段裁剪（只保留业务需要的字段）再交给模型。
16. `list-nutrition-foods` 返回压缩表格字符串，需自写解析器。
17. 营养品名与菜单 code 无直接映射，做联动需维护映射表。

### 6.5 数据时效性
18. 门店**非营业时间**查菜单会报 600057（实测 23:50 失败），Skill 里要有兜底话术。
19. 促销规则（query-promotions）返回的是原始规则数据，满减/满折需自行计算。
20. 实测 `query-store-coupons` 在部分门店返回空数组 `[]`，属正常结果，不等于失败。

---

## 七、对参赛项目的三条建议

1. **优先用「活动/派对」和「抽奖」这类新上线的冷门工具**——35 个工具里大多数参赛者只会用 query-meals + create-order，派对链路（6 个工具严格串行）和积分链路（6 个工具）能做出差异化。
2. **把「统一信封 + success 判断」写成 Skill 的硬性检查逻辑**（确定性逻辑不要交给模型），否则会出现「明明下单失败却告诉用户成功」的严重问题。
3. **必须做 payload 裁剪**，尤其是券和活动日历两个接口，否则多轮对话很快超出上下文。
