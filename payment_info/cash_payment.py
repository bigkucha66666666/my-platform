"""Versioned cash settlement for the formal dynamic-bottleneck experiment."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


RULE_VERSION = 'dynamic_cost_v1'
FORMAL_ROUNDS = 30
BASE_CENTS = 1500
MAX_BONUS_CENTS = 2000


@dataclass(frozen=True)
class CashPayment:
    rule_version: str
    formal_rounds: int
    total_cost: Decimal
    mean_cost: Decimal
    base_cents: int
    bonus_cents: int
    total_cents: int


def calculate_cash_payment(*, total_cost, formal_rounds):
    """Return cash cents; a mean cost of 20 yields CNY 25."""
    if formal_rounds != FORMAL_ROUNDS:
        raise ValueError('Cash settlement requires all 30 formal rounds.')
    try:
        cost = Decimal(str(total_cost))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError('Formal total cost must be a finite nonnegative number.') from exc
    if not cost.is_finite() or cost < 0:
        raise ValueError('Formal total cost must be a finite nonnegative number.')

    mean_cost = cost / Decimal(FORMAL_ROUNDS)
    bonus_cap_yuan = Decimal(MAX_BONUS_CENTS) / 100
    bonus_yuan = max(
        Decimal(0),
        min(bonus_cap_yuan, bonus_cap_yuan - mean_cost / 2),
    )
    bonus_cents = int(
        (bonus_yuan * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    )
    return CashPayment(
        rule_version=RULE_VERSION,
        formal_rounds=FORMAL_ROUNDS,
        total_cost=cost,
        mean_cost=mean_cost,
        base_cents=BASE_CENTS,
        bonus_cents=bonus_cents,
        total_cents=BASE_CENTS + bonus_cents,
    )
