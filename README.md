# 麦麦明白点 · mcd-plain-order

> **说一句话，明明白白点好一餐。**
> 对话式麦当劳点餐助手，覆盖「选餐 → 算价 → 下单 → 取餐 → 售后」，对齐工信部适老化设计规范。

本项目参加 [麦当劳程序员节创意开发大赛](https://github.com/M-China/mcd-developer-innovation-challenge)，作品形态为 **WorkBuddy Skill**。

---

## 能做 / 不做

- **能做**：到店自取 / 得来速 / 麦乐送 / 企业团餐四种取餐方式的选餐、算价、下单、查单、取消；自动判断取餐方式；配券与多方案真实计价；预约到店即取。
- **不做**：库存 / 排班 / 销量（MCP 无此数据）、排队预测、品牌对比、占座类场景。
- **适合**：想快速点一餐的通勤 / 早餐人群；不熟悉多步操作的用户；替长辈点餐的子女。

## 怎么用（输入 → 输出）

```
用户：帮我点份早餐，能用券的
助手：
🍔 极速点单（到店自取 · 预计 9 分钟到店）
· 厚松饼猪柳蛋套餐 ×1
────────────
原价 ¥16.5
优惠 ¥6.6
实付 ¥9.9

📍 最近门店：长兴路得来速（605 米）· 预约 09:10 到店
👉 确认下单？

用户：确认
助手：已完成，实付 ¥9.9。如需取消，请在 10 分钟内告诉我「取消这一单」，我会帮你退掉。
```

完整的输入字段、输出格式与边界阈值见 [`SKILL.md`](SKILL.md)；消息格式细则见 [`references/output-format.md`](references/output-format.md)。

## 目录结构

```
.
├── SKILL.md                        作品本体：Skill 运行规范
├── scripts/                        确定性逻辑（仅标准库）
│   ├── mcd_client.py               MCP 客户端（鉴权 / 会话 / 业务状态校验）
│   ├── router.py                   取餐方式路由与参数构造
│   ├── coupons.py                  券可用性判定（日内窗口）
│   ├── guard.py                    写操作闸门与回执话术
│   ├── personalize.py / render.py / newitems.py / reminders.py
│   └── fast_order.py / workflow.py 极速点单编排（确定性 + 网络委托）
├── references/                     参考文档（按需加载）
│   ├── product-spec.md / design-standards.md / architecture.md
│   ├── output-format.md
│   ├── be_type_matrix.md / error_codes.md
│   └── mcd-mcp-tools-reference.md
├── tests/                          126 个单测（确定性逻辑）
├── README.md / MCP_INTEGRATION.md / CONTEST_DECLARATION.md
├── mcp-config.example.json / workbuddy.md
└── .env.example / .gitignore
```

## 安装方法

1. 申请麦当劳 MCP Token：https://open.mcd.cn/mcp
2. 在 WorkBuddy 中配置自定义连接器，填入 `mcp-config.example.json` 的内容，把 `${MCD_MCP_TOKEN}` 替换为真实 Token，并启用
3. 将本仓库复制到 WorkBuddy 技能目录：

```bash
# 用户级（所有项目可用）
cp -r . ~/.workbuddy/skills/mcd-plain-order/

# 或项目级（随仓库共享）
cp -r . <你的项目>/.workbuddy/skills/mcd-plain-order/
```

## 配置

```bash
cp .env.example .env
# 编辑 .env，填入真实 MCD_MCP_TOKEN —— 切勿提交到仓库
```

> ⚠️ `Authorization` 的格式是 `Bearer <Token>`，**`Bearer ` 前缀和后面的空格都不能少**（少了会返回 403 + code 400003）。

## 依赖

- Python 3.10+，**仅标准库**，无需 `pip install`
- 运行全部单测：`python -m unittest discover -s tests`（126 个）
- 自检：`python scripts/mcd_client.py list-tools`（应返回 35 个工具）

## 设计标准

对齐《移动互联网应用（APP）适老化通用设计规范》（工信部）作为设计参照：金额三行（原价 / 优惠 / 实付）、禁用专业词与网络新词、写操作必须可撤销并写明时限、无诱导付款、无默认勾选、Token 与个人数据不入库。完整可验收清单见 [`references/design-standards.md`](references/design-standards.md)。

> 诚实说明：本项目**不声称**通过工信部适老化评测，规范在此作为**设计参照标准**使用。

## 边界

不做库存 / 排班 / 销量、排队预测、品牌对比、占座类场景。详见 [`references/product-spec.md`](references/product-spec.md) 的「非目标」一节。

## 许可证

<!-- TODO：建议补充开源许可证，如 MIT -->
