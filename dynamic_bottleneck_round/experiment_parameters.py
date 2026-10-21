"""Versioned costs and population-based choice-window endpoints."""
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR

EXPERIMENT_PARAMETERS_VAR = 'dynamic_bottleneck_experiment_parameters_v2'
EXPERIMENT_PARAMETERS_VERSION = 'mean_window_late4_v1'
ADAPTIVE_BANK_FINGERPRINTS_VAR = 'dynamic_bottleneck_adaptive_rl_fingerprints_v1'


def _number(value, name, *, positive=False):
    if isinstance(value, bool):
        raise ValueError(f'{name} must be a finite number')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f'{name} must be a finite number') from exc
    if not result.is_finite() or (positive and result <= 0):
        raise ValueError(f'{name} must be finite and positive')
    return result


def calculate_departure_window(*, actor_count, mean_capacity,
                               standard_minute=480, early_cost_rate=1,
                               late_cost_rate=4, step_minutes=1,
                               free_flow_minutes=6):
    """Derive departure endpoints from 08:00 arrival, then round outwards.

    The continuous fixed-capacity formula supplies a choice-set reference;
    replacing capacity by its mean is not a stochastic-equilibrium solution.
    """
    count = _number(actor_count, 'actor_count', positive=True)
    step = _number(step_minutes, 'step_minutes', positive=True)
    if count != count.to_integral_value() or step != step.to_integral_value():
        raise ValueError('actor_count and step_minutes must be positive integers')
    capacity = _number(mean_capacity, 'mean_capacity', positive=True)
    early = _number(early_cost_rate, 'early_cost_rate', positive=True)
    late = _number(late_cost_rate, 'late_cost_rate', positive=True)
    standard = _number(standard_minute, 'standard_minute')
    free_flow = _number(free_flow_minutes, 'free_flow_minutes')
    if free_flow < 0:
        raise ValueError('free_flow_minutes must be nonnegative')
    span = count / capacity
    raw_start = standard - free_flow - late / (early + late) * span
    raw_end = standard - free_flow + early / (early + late) * span
    first = int(step * (raw_start / step).to_integral_value(rounding=ROUND_FLOOR))
    last = int(step * (raw_end / step).to_integral_value(rounding=ROUND_CEILING))
    return dict(raw_start_minute=float(raw_start), raw_end_minute=float(raw_end),
                first_departure_minute=first, last_departure_minute=last,
                num_slots=(last - first) // int(step) + 1,
                slot_size_minutes=int(step))


def new_experiment_parameters(*, mean_capacity=2.665):
    capacity = _number(mean_capacity, 'mean_capacity', positive=True)
    return dict(version=EXPERIMENT_PARAMETERS_VERSION, mean_capacity=float(capacity),
                preferred_arrival_minute=480, free_flow_travel_minutes=6,
                fixed_travel_time_cost=0, queue_cost_per_minute=2,
                early_cost_per_minute=1, late_cost_per_minute=4,
                departure_step_minutes=1, window_standard_minute=480)


def experiment_parameters_for_session(session):
    if session is None:
        return new_experiment_parameters()
    stored = getattr(session, '_vars', None)
    if stored is None:
        stored = getattr(session, 'vars', {})
    if EXPERIMENT_PARAMETERS_VAR not in stored:
        return {**new_experiment_parameters(), 'version': 'legacy_fixed16_late3',
                'late_cost_per_minute': 3}
    parameters = stored[EXPERIMENT_PARAMETERS_VAR]
    if not isinstance(parameters, dict) or parameters.get('version') != EXPERIMENT_PARAMETERS_VERSION:
        raise ValueError('Invalid saved experiment parameters')
    required = new_experiment_parameters().keys()
    if not all(key in parameters for key in required):
        raise ValueError('Incomplete saved experiment parameters')
    return deepcopy(parameters)
