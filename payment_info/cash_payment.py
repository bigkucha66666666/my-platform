"""Versioned cash settlement for the formal dynamic-bottleneck experiment."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


RULE_VERSION = 'dynamic_cost_v1'
BUDGET_RULE_VERSION = 'dynamic_cost_budget_v2'
RANK_RULE_VERSION = 'dynamic_cost_rank_v3'
FORMAL_ROUNDS = 30
BASE_CENTS = 1500
MAX_BONUS_CENTS = 2000
BUDGET_HUMAN_LIMIT = 80
BUDGET_TOTAL_CENTS = 200000


@dataclass(frozen=True)
class CashRule:
    version: str
    max_bonus_cents: int
    cost_divisor: int | None
    budget_human_limit: int | None = None
    budget_total_cents: int | None = None
    rank_bonus_cap_cents: int = 0

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
    RANK_RULE_VERSION: CashRule(RANK_RULE_VERSION, 1500, None, 80, 240000, 300),
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
    cost_bonus_cents: int = 0
    rank_bonus_cents: int = 0
    rank: int | None = None
    rank_tied_count: int | None = None
    rank_actor_count: int | None = None
    rank_midpoint: Decimal | None = None
    rank_percentile: Decimal | None = None
    rank_includes_agents: bool = False


def _round_cents(value):
    return int(value.quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def _final_rank(ranking, cost):
    from dynamic_bottleneck_round.result_ranking import RANKING_RULE_VERSION
    if (not isinstance(ranking, dict)
            or ranking.get('rule_version') != RANKING_RULE_VERSION
            or type(ranking.get('completed_rounds')) is not int
            or ranking['completed_rounds'] != FORMAL_ROUNDS):
        raise ValueError('Cash settlement requires the final 30-round ranking.')
    values = [ranking.get(key) for key in ('rank', 'tied_count', 'actor_count')]
    if any(type(value) is not int or value < 1 for value in values):
        raise ValueError('Invalid final rank or market size.')
    rank, tied, count = values
    if rank + tied - 1 > count:
        raise ValueError('Tied ranks exceed the market roster.')
    try:
        rank_cost = Decimal(str(ranking.get('total_cost')))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError('Invalid final ranking cost.') from exc
    if not rank_cost.is_finite() or rank_cost != cost:
        raise ValueError('Final ranking and cash costs must agree.')
    midpoint = Decimal(rank) + Decimal(tied - 1) / 2
    percentile = (Decimal(count) - midpoint) / (count - 1) if count > 1 else Decimal('.5')
    return rank, tied, count, midpoint, percentile


def calculate_cash_payment(*, total_cost, formal_rounds, rule_version=RULE_VERSION,
                           ranking=None):
    """Versioned cents, retaining v1 for legacy callers; Sessions pass their rule."""
    rule = cash_rule_for_version(rule_version)
    if type(formal_rounds) is not int or formal_rounds != FORMAL_ROUNDS:
        raise ValueError('Cash settlement requires all 30 formal rounds.')
    try:
        cost = Decimal(str(total_cost))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError('Formal total cost must be a finite nonnegative number.') from exc
    if not cost.is_finite() or cost < 0:
        raise ValueError('Formal total cost must be a finite nonnegative number.')

    mean_cost = cost / Decimal(FORMAL_ROUNDS)
    if rule.version == RANK_RULE_VERSION:
        rank, tied, count, midpoint, percentile = _final_rank(ranking, cost)
        # 100 * (12 - .3 * total / 30) == 1200 - total exactly.
        # Avoid a repeating mean creating an artificial half-cent error.
        cost_cap = Decimal(rule.max_bonus_cents - rule.rank_bonus_cap_cents)
        rank_cap = Decimal(rule.rank_bonus_cap_cents)
        cost_bonus = _round_cents(max(Decimal(0), cost_cap - cost))
        rank_bonus = _round_cents(
            rank_cap * (Decimal(count) - midpoint) / (count - 1)
            if count > 1 else rank_cap / 2)
        return CashPayment(
            rule_version=rule.version, formal_rounds=FORMAL_ROUNDS,
            total_cost=cost, mean_cost=mean_cost, base_cents=BASE_CENTS,
            bonus_cents=cost_bonus + rank_bonus,
            total_cents=BASE_CENTS + cost_bonus + rank_bonus,
            cost_bonus_cents=cost_bonus, rank_bonus_cents=rank_bonus,
            rank=rank, rank_tied_count=tied, rank_actor_count=count,
            rank_midpoint=midpoint, rank_percentile=percentile,
            rank_includes_agents=bool(ranking.get('includes_agents')),
        )
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
        cost_bonus_cents=bonus_cents,
    )


def cash_payment_description(rule_version):
    """One source for the pre-decision disclosure and the final receipt."""
    rule = cash_rule_for_version(rule_version)
    if rule.version == RANK_RULE_VERSION:
        return (
            '完成后可获得基础报酬 15 元，另有成本奖金 0–12 元、排名奖金 0–3 元。'
            '成本奖金 = max(0, 12 − 0.3 × 正式30轮平均成本)。'
            '排名按正式30轮累计成本由低到高，在本组全部主体中计算，包含真人和 Agent；'
            '热身轮不计入，人数按本组实际配置固定，自动代选轮仍计入成本。'
            '排名奖金 = 3 × (组内人数 − 平均名次) ÷ (组内人数 − 1)。'
            '并列者平分所占名次的排名奖金：平均名次 = 显示名次 + (并列人数 − 1) ÷ 2；'
            '排名比较采用累计成本保留6位小数。所有人并列或本组仅1人时，排名奖金为1.50元。'
            'Agent不领取现金，其对应奖金不重分配给真人。'
            '两项奖金分别四舍五入到分后相加，最终合计最低 15 元、最高 30 元。'
            '例如平均成本20、9人组内第5名且无并列，成本奖金6元、排名奖金1.50元，合计22.50元。'
            '每轮显示的累计排名仅为进度，最终报酬在30轮结束后结算。'
        )
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
