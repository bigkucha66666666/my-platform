"""Versioned cash settlement for the formal dynamic-bottleneck experiment."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


RULE_VERSION = 'dynamic_cost_v1'
BUDGET_RULE_VERSION = 'dynamic_cost_budget_v2'
FORMAL_ROUNDS = 30
BASE_CENTS = 1500
MAX_BONUS_CENTS = 2000
BUDGET_HUMAN_LIMIT = 80
BUDGET_TOTAL_CENTS = 200000


@dataclass(frozen=True)
class CashRule:
    version: str
    max_bonus_cents: int
    cost_divisor: int
    budget_human_limit: int | None = None
    budget_total_cents: int | None = None

    @property
    def max_total_cents(self):
        return BASE_CENTS + self.max_bonus_cents


_RULES = {
    RULE_VERSION: CashRule(RULE_VERSION, MAX_BONUS_CENTS, 2),
    # Sufficient for <=80 Humans across independent rooms; not a shared purse.
    BUDGET_RULE_VERSION: CashRule(
        BUDGET_RULE_VERSION, BUDGET_TOTAL_CENTS // BUDGET_HUMAN_LIMIT - BASE_CENTS,
        4, BUDGET_HUMAN_LIMIT, BUDGET_TOTAL_CENTS,
    ),
}


def cash_rule_for_version(version):
    if not isinstance(version, str) or version not in _RULES:
        raise ValueError(f'未知的动态场景报酬规则：{version!r}')
    return _RULES[version]


@dataclass(frozen=True)
class CashPayment:
    rule_version: str
    formal_rounds: int
    total_cost: Decimal
    mean_cost: Decimal
    base_cents: int
    bonus_cents: int
    total_cents: int


def calculate_cash_payment(*, total_cost, formal_rounds, rule_version=RULE_VERSION):
    """Versioned cents, retaining v1 for legacy callers; Sessions pass their rule."""
    rule = cash_rule_for_version(rule_version)
    if formal_rounds != FORMAL_ROUNDS:
        raise ValueError('Cash settlement requires all 30 formal rounds.')
    try:
        cost = Decimal(str(total_cost))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError('Formal total cost must be a finite nonnegative number.') from exc
    if not cost.is_finite() or cost < 0:
        raise ValueError('Formal total cost must be a finite nonnegative number.')

    mean_cost = cost / Decimal(FORMAL_ROUNDS)
    bonus_cap_yuan = Decimal(rule.max_bonus_cents) / 100
    bonus_yuan = max(
        Decimal(0),
        min(bonus_cap_yuan, bonus_cap_yuan - mean_cost / rule.cost_divisor),
    )
    bonus_cents = int(
        (bonus_yuan * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    )
    return CashPayment(
        rule_version=rule.version,
        formal_rounds=FORMAL_ROUNDS,
        total_cost=cost,
        mean_cost=mean_cost,
        base_cents=BASE_CENTS,
        bonus_cents=bonus_cents,
        total_cents=BASE_CENTS + bonus_cents,
    )


def cash_payment_description(rule_version):
    """One source for the pre-decision disclosure and the final receipt."""
    rule = cash_rule_for_version(rule_version)
    reference = calculate_cash_payment(total_cost=20 * FORMAL_ROUNDS,
        formal_rounds=FORMAL_ROUNDS, rule_version=rule_version)
    return (
        f'完成后可获得基础报酬 {BASE_CENTS / 100:g} 元，'
        f'另有绩效报酬 0–{rule.max_bonus_cents / 100:g} 元。'
        f'绩效只依据你自己的正式 {FORMAL_ROUNDS} 轮平均成本：'
        f'平均成本为 20 时，合计为 {reference.total_cents / 100:g} 元；'
        f'在绩效变化区间内，平均成本每降低 {rule.cost_divisor} 点，合计增加 1 元，'
        f'每提高 {rule.cost_divisor} 点则减少 1 元，'
        f'最终合计最低 {BASE_CENTS / 100:g} 元、最高 {rule.max_total_cents / 100:g} 元。'
        '热身轮不计入。'
    )
