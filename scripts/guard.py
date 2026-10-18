#!/usr/bin/env python3
"""写操作安全闸门。

为什么需要它：
- 下单 / 取消 / 抽奖等动作不可撤销或涉及真实扣费；
- 工信部适老化规范要求：涉及财务交易的提交应可逆，并可撤销；
- 把「必须确认」写成代码，而不是指望模型每次都记得。

默认值一律是「未确认」，未确认即拦截。任何拦截都必须说明原因（失败显性化）。
"""

from __future__ import annotations

from dataclasses import dataclass

# 所有会改变用户状态或产生费用的工具。新增写操作必须登记在这里，
# 否则 check() 会以「未知的写操作」拦截（宁可拦住，不可放过）。
WRITE_TOOLS: frozenset[str] = frozenset(
    {
        "create-order",
        "cancel-order",
        "mall-create-order",
        "party-order-create",
        "auto-bind-coupons",
        "draw-lottery",
        "delivery-create-address",
    }
)

# 金额超过该阈值时，即使是已确认的操作也要求二次确认。
DEFAULT_AMOUNT_LIMIT = 200.0

# 企业团餐单笔天然较大（且 ¥300 起送），单次上限相应抬高，
# 避免正常团餐被默认 200 上限误拦（仍可被显式 amount_limit 覆盖）。
GROUP_AMOUNT_LIMIT = 5000.0


def limit_for_method(method: str) -> float:
    """按取餐方式给出合理的单次金额上限（用户意图驱动）。

    - 企业团餐（group）：单笔通常 ≥¥300，抬高到 5000，避免与默认 200 冲突；
    - 其余方式：维持默认 200。
    该值仅作为 run_fast_order 未显式传 amount_limit 时的兜底，显式传入优先。
    """
    return GROUP_AMOUNT_LIMIT if method == "group" else DEFAULT_AMOUNT_LIMIT


# 各写操作可接受的"用户确认语"关键词。调用方必须把用户在对话里的原话传入 user_said，
# 由本模块判断是否包含明确意图——避免把"外带吧"这类参数回答误当成下单确认。
_CONFIRM_WORDS: dict[str, tuple[str, ...]] = {
    "create-order": ("下单", "确认", "要了", "买了", "就这个", "来一份", "来一个"),
    "party-order-create": ("下单", "预约", "确认", "要了"),
    "mall-create-order": ("兑换", "下单", "确认", "要了"),
    "cancel-order": ("取消", "退掉", "不要了", "别下了", "撤单", "确认"),
    "auto-bind-coupons": ("领", "确认", "要"),
    "draw-lottery": ("抽", "确认", "要"),
    "delivery-create-address": ("创建", "新增", "保存", "确认"),
}


def has_confirm_intent(tool: str, user_said: str) -> bool:
    """用户这句话是否包含该写操作的明确确认意图。"""
    words = _CONFIRM_WORDS.get(tool, ("确认", "要", "好"))
    return any(w in user_said for w in words)


class GuardError(Exception):
    """闸门拦截。message 必须能直接展示给用户。"""


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    reason: str = ""


def is_write_tool(tool: str) -> bool:
    return tool in WRITE_TOOLS


def check(
    tool: str,
    args: dict | None = None,
    *,
    confirmed: bool = False,
    user_said: str | None = None,
    amount: float | None = None,
    amount_limit: float = DEFAULT_AMOUNT_LIMIT,
    required_fields: tuple[str, ...] | None = None,
    preflight_done: bool = False,
) -> CheckResult:
    """检查一次写操作是否放行。

    参数：
      confirmed       用户是否已明确同意（默认 False = 拦截）
      user_said       用户在对话里的确认原话（如「下单」）；写操作必填，由代码校验意图
      amount          本次操作的实付金额（元）
      required_fields 该工具的业务必填字段
      preflight_done  draw-lottery 专用：是否已展示过 drawDecision.nextConsumption
    """
    args = args or {}

    # 未知工具直接拦截：宁可拦住，不可放过
    if not is_write_tool(tool):
        return CheckResult(False, f"未知的写操作：{tool}，已拦截。请在 WRITE_TOOLS 中登记后再调用。")

    if not confirmed:
        return CheckResult(False, "这是会产生实际费用的操作，需要你确认后才会执行。")

    # 确认不能只靠模型自报：必须带上用户在对话里的原话，且原话要有明确意图。
    if not user_said:
        return CheckResult(
            False,
            "缺少用户确认语：请把用户明确说「下单 / 确认」的原话传入 user_said，再执行。",
        )
    if not has_confirm_intent(tool, user_said):
        return CheckResult(
            False,
            f"用户这句话「{user_said}」不像是明确的确认，请再问一次（例如「下单」「确认」）。",
        )

    for field in required_fields or ():
        if args.get(field) in (None, "", [], {}):
            return CheckResult(False, f"缺少必要信息：{field}，无法执行。")

    if amount is not None and amount > amount_limit:
        return CheckResult(
            False,
            f"本次金额 ¥{amount:.2f} 超过单次上限 ¥{amount_limit:.2f}，请再次确认后再执行。",
        )

    if tool == "draw-lottery" and not preflight_done:
        return CheckResult(
            False,
            "抽奖前需要先查看本次消耗规则并确认，不能直接开抽。",
        )

    return CheckResult(True)


def require(tool: str, args: dict | None = None, **kwargs) -> None:
    """check 的抛异常版本，供确定性流程直接使用。"""
    result = check(tool, args, **kwargs)
    if not result.ok:
        raise GuardError(result.reason)


def receipt_notice(amount: float, *, window_minutes: int = 10) -> str:
    """下单回执中必须包含的撤销提示（设计标准 P0 项，由单测断言）。"""
    return (
        f"已完成，实付 ¥{amount:.2f}。\n"
        f"如需取消，请在 {window_minutes} 分钟内告诉我「取消这一单」，我会帮你退掉。"
    )
