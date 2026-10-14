# 取餐方式参数矩阵

> 全部来自实测（真实 Token，2026-10-11），非文档推测。
> **配错必报 `code=60002`：未查询到门店信息, 检查 storeCode 和 beCode 是否配对。**

---

## 一、核心配对表

| 取餐方式 | beType | orderType | beCode | beCode 来源 | 额外必填 |
|---|---|---|---|---|---|
| **到店自取** | 1 | 1 | **不传**（传了报错） | — | `takeWayCode`（来自计价） |
| **得来速 DT** | 5 | 1 | **必传** | `query-nearby-stores` | `takeWayCode` |
| **麦乐送** | 2 | 2 | **必传** | `delivery-query-stores` | `addressId`；`remark` 可选（≤50 字） |
| **企业团餐** | 6 | 2 | **必传** | `delivery-query-stores` | `addressId` + `gmServiceCode`（来自 `query-meal-assistance`） |

---

## 二、各方法的完整调用顺序

### 2.1 到店自取

```
query-nearby-stores(beType=1, searchType=2, city, keyword)  → storeCode
  ↓
query-meals(storeCode, beType=1, orderType=1)               → 餐品 code
query-store-coupons(storeCode, beType=1, orderType=1)       → 可用券
  ↓
calculate-price(storeCode, beType=1, orderType=1, items)    → 金额 + takeWayList
  ↓
create-order(storeCode, beType=1, orderType=1, items, takeWayCode)
```

### 2.2 得来速

```
query-nearby-stores(beType=5, searchType=2, city, keyword)  → storeCode + beCode
  ↓
query-meals(storeCode, beType=5, orderType=1, beCode)       ← beCode 必传
  ↓
calculate-price(..., beCode)  → takeWayList
  ↓
create-order(..., beCode, takeWayCode)
```

### 2.3 麦乐送

```
delivery-query-addresses()                                  → addressId
  ↓
delivery-query-stores(addressId, beType=2)                  → storeCode + beCode
  ↓
query-meals(storeCode, beType=2, orderType=2, beCode)
  ↓
calculate-price(..., beCode, items)
  ↓
create-order(..., addressId, beCode, items, remark?)
```

### 2.4 企业团餐

```
delivery-query-addresses()                                  → addressId
delivery-query-stores(addressId, beType=6)                  → storeCode + beCode
  ↓
query-meal-assistance(storeCode, beType=6, orderType=2, beCode)  → gmServiceCode
query-promotions(storeCode, beCode, beType=6, orderType=2)       → 满减规则（需自行解析）
  ↓
query-meals(...)  → 参考 description 内置的预算搭配规则：
   <20元 小食 ｜ 20-30 汉堡+小食或饮料 ｜ 30-40 汉堡+薯条/小食+饮料
   40-50 汉堡+薯条+小食+饮料 ｜ 50+ 丰富组合（优先不重复小食）
  ↓
calculate-price(..., gmServiceCode)
  ↓
create-order(..., gmServiceCode)
```

> ✅ **已在 `scripts/workflow.py::run_fast_order` 落地**：`method="group"` 分支会自动
> `delivery-query-stores(beType=6)` → `query-meal-assistance`（取 `gmServiceCode`）→ `calculate-price`（带 `gmServiceCode`）
> → `create-order`（带 `beCode`+`gmServiceCode`+`addressId`）。团餐与麦乐送同为 orderType=2，不传 `takeWayCode`/`reservationDate`。
> 离线单测（`tests/test_workflow.py::TestGroupFlow`）已覆盖全字段透传与"meal-assistance 先于 price"顺序约束。

---

## 三、两条正交维度

| 维度 | 规则 |
|---|---|
| **预约** | 传 `reservationDate`，格式 `yyyy-MM-dd HH:mm`；**非预约场景不要传** |
| **套餐改配** | `query-meal-detail` 的 `rounds[].choices[].code`；1.0.5+ 支持换配与部分特制 |

---

## 四、易错点清单（实测踩坑）

| # | 易错点 | 后果 |
|---|---|---|
| 1 | 到店场景传了 `beCode` | 报错（官方明确注明"传了会报错"） |
| 2 | 外送场景忘了 `beCode` | `60002` |
| 3 | `takeWayCode` 自己编 | 下单失败；**只能取 `calculate-price` 的 `takeWayList[].code`** |
| 4 | 到店场景传了 `takeWayCode` 之外的写参数 | 参数不生效或被拒 |
| 5 | 团餐漏了 `gmServiceCode` | 下单失败 |
| 6 | 用 `query-my-coupons` 的券去下单 | 券可能门店不可用；**必须用 `query-store-coupons` 校验** |
| 7 | 非预约场景传了 `reservationDate` | 行为未定义，避免传 |
| 8 | 用 `orderId` 猜值 | 官方明确"Agent 不要自行生成" |
