"""Private cumulative-cost rankings, independent of cash or policy learning."""
from collections import Counter
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


RANKING_RULE_VAR = 'dynamic_bottleneck_ranking_rule_v1'
RANKING_RULE_VERSION = 'formal_cumulative_cost_v1'
RANKING_STATE_VAR = 'dynamic_bottleneck_ranking_state_v1'
RANKING_RESULTS_VAR = 'dynamic_bottleneck_ranking_results_v1'


def saved_vars(owner):
    """Reading oTree .vars dirties its ORM field; use its backing value."""
    stored = getattr(owner, '_vars', None)
    return stored if stored is not None else getattr(owner, 'vars', {})


def ranking_enabled(session):
    return saved_vars(session).get(RANKING_RULE_VAR) == RANKING_RULE_VERSION


def _cost(value):
    if isinstance(value, bool):
        raise ValueError('Ranking cost must be a finite nonnegative number')
    try:
        cost = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError('Invalid ranking cost') from exc
    if not cost.is_finite() or cost < 0:
        raise ValueError('Ranking cost must be a finite nonnegative number')
    return cost


def accumulate_rankings(current_costs, *, completed_rounds, previous_totals=None):
    """Keep full totals; competition ranks compare costs to six decimals."""
    if (isinstance(completed_rounds, bool) or not isinstance(completed_rounds, int)
            or not 1 <= completed_rounds <= 30):
        raise ValueError('Ranking requires 1..30 completed formal rounds')
    if not isinstance(current_costs, dict) or not current_costs:
        raise ValueError('Ranking requires a nonempty actor roster')
    if any(not isinstance(key, str) or not key for key in current_costs):
        raise ValueError('Ranking actor identities must be nonempty strings')
    if completed_rounds == 1:
        if previous_totals is not None:
            raise ValueError('First formal round cannot include earlier costs')
        previous = dict.fromkeys(current_costs, Decimal(0))
    else:
        if not isinstance(previous_totals, dict) or previous_totals.keys() != current_costs.keys():
            raise ValueError('Every actor needs the same complete formal history')
        previous = {key: _cost(value) for key, value in previous_totals.items()}
    totals = {key: previous[key] + _cost(value) for key, value in current_costs.items()}
    try:
        comparison = {key: total.quantize(Decimal('.000001'), rounding=ROUND_HALF_UP)
                      for key, total in totals.items()}
        counts = Counter(comparison.values())
        return dict(
            completed_rounds=completed_rounds,
            actor_count=len(totals),
            totals={key: str(value) for key, value in totals.items()},
            rankings={key: dict(
                rank=1 + sum(other < comparison[key] for other in comparison.values()),
                tied_count=counts[comparison[key]],
                mean_cost=str((total / completed_rounds).quantize(Decimal('.0001'), rounding=ROUND_HALF_UP)),
                total_cost=str(total),
            ) for key, total in totals.items()},
        )
    except InvalidOperation as exc:
        raise ValueError('Ranking cost is outside supported precision') from exc
