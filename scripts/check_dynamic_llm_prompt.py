"""Opt-in six-call v1/v2 smoke check; no Session/database/training bank access.

Run from the project root with ``python -m scripts.check_dynamic_llm_prompt
--real-api --output output/<new-name>.json``. Existing output is never overwritten.
This checks responses, not a causal treatment effect or long-run policy stability.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import random
from statistics import mean

import dynamic_bottleneck_round as experiment
from dynamic_bottleneck_round.agents.deepseek_agent import (
    AgentChoiceSet, choose_agent_departure, config_from_session, post_chat_completion,
)
from dynamic_bottleneck_round.agents.personas import PERSONA_LIBRARY
from dynamic_bottleneck_round.agents.prompts import CURRENT_PROMPT_VERSION, LEGACY_PROMPT_VERSION
from dynamic_bottleneck_round.stochastic_capacity import (
    StochasticCapacityConfig, capacity_level, stochastic_capacity_public_context,
    truncated_normal_quantile,
)


SIMULATION_SEED = 2026101501
VERSIONS = (LEGACY_PROMPT_VERSION, CURRENT_PROMPT_VERSION)
ROUND_COUNT = 3


def generate_scenarios():
    rng = random.Random(SIMULATION_SEED)
    scenarios = []
    for number in range(1, ROUND_COUNT + 1):
        capacity = truncated_normal_quantile(rng.random())
        opponents = [rng.randint(1, 16) if actor % 3 == 0 else
                     min(16, max(1, round(rng.gauss(8, 2.5)))) for actor in range(29)]
        scenarios.append(dict(round_number=number, capacity=capacity, opponent_slots=opponents))
    return scenarios


def settle_market(capacity, slots):
    """Full outcomes using production arithmetic; never write experiment objects."""
    if len(slots) != 30 or any(slot not in range(1, 17) for slot in slots):
        raise ValueError('Independent check needs 30 actors and slots 1..16')
    by_slot = {}
    for index, slot in enumerate(slots):
        by_slot.setdefault(slot, []).append(index)
    results = [None] * len(slots)
    next_available = 466.0
    for slot, indices in sorted(by_slot.items()):
        departure = 465.0 + slot
        first_service = max(departure, next_available)
        delay = experiment.service_batch_wait_minutes(
            departure_minute=departure, first_service_start_minute=first_service,
            load=len(indices), capacity=capacity,
            capacity_window_minutes=experiment.C.CAPACITY_WINDOW_MINUTES)
        arrival = departure + experiment.C.FREE_FLOW_TRAVEL_MINUTES + delay
        early = max(0, experiment.C.PREFERRED_ARRIVAL_MINUTE - arrival)
        late = max(0, arrival - experiment.C.PREFERRED_ARRIVAL_MINUTE)
        costs = experiment.calculate_cost_components(
            queue_delay=delay, early_minutes=early, late_minutes=late)
        for index in indices:
            results[index] = dict(departure_slot=slot, departure_minute=departure,
                departure_time_label=experiment.minute_to_clock(departure),
                arrival_minute=arrival, arrival_time_label=experiment.minute_to_clock(arrival),
                queue_delay_minutes=delay, travel_time_minutes=arrival - departure,
                early_minutes=early, late_minutes=late, slot_load=len(indices),
                total_cost=costs['total_cost'])
        next_available = first_service + len(indices) / capacity
    return results


def public_feedback(number, capacity, outcomes, capacity_config):
    costs_by_slot = {slot: [] for slot in range(1, 17)}
    for outcome in outcomes:
        costs_by_slot[outcome['departure_slot']].append(outcome['total_cost'])
    return dict(round_number=number, actual_capacity=capacity, dynamic_capacity=capacity,
        capacity_level=capacity_level(capacity), capacity_distribution=capacity_config.distribution,
        capacity_mu=capacity_config.capacity_mu, capacity_sigma=capacity_config.capacity_sigma,
        capacity_truncated_mean=capacity_config.truncated_mean,
        capacity_truncated_sd=capacity_config.truncated_standard_deviation,
        capacity_min=capacity_config.capacity_min, capacity_max=capacity_config.capacity_max,
        information_condition='I0', group_average_cost=round(mean(o['total_cost'] for o in outcomes), 4),
        departure_outcomes=[dict(slot=slot, departure_minute=465 + slot,
            departure_time=experiment.minute_to_clock(465 + slot), participant_count=len(costs),
            average_cost=round(mean(costs), 4) if costs else None)
            for slot, costs in costs_by_slot.items()])


def run_comparison(config, *, http_post=None, on_progress=None):
    """Exactly three rounds × two versions, at most one transport call each."""
    scenarios = generate_scenarios()
    report = dict(started_utc=datetime.now(timezone.utc).isoformat(),
        purpose='independent simulation smoke check, not formal experiment data',
        simulation_seed=SIMULATION_SEED, information_condition='I0', actor_count=30,
        persona=deepcopy(next(p for p in PERSONA_LIBRARY if p['persona_id'] == 'adaptive_v1')),
        request_parameters=dict(model=config.model, temperature=config.temperature,
            thinking_enabled=config.thinking_enabled, max_tokens=config.max_tokens,
            timeout_seconds=config.timeout_seconds, limited_memory_enabled=config.limited_memory_enabled,
            limited_memory_max_chars=config.limited_memory_max_chars),
        scenarios=scenarios, records=[], summary={})
    histories = {version: {'public_feedback': None, 'own_previous_result': None} for version in VERSIONS}
    memories = {version: '' for version in VERSIONS}
    capacity_config = StochasticCapacityConfig(information_condition='I0')
    transport = http_post or post_chat_completion
    for scenario in scenarios:
        number, capacity = scenario['round_number'], scenario['capacity']
        for version in VERSIONS:
            current_config = replace(config, prompt_version=version)
            capacity_context = stochastic_capacity_public_context(capacity_config,
                {'actual_capacity': capacity})
            capacity_context.update(experiment_phase='formal', round_label=f'正式第 {number} 轮',
                capacity_states=experiment.capacity_state_rows(capacity_config), capacity_reveal_timing='I0')
            choice_set = AgentChoiceSet(round_number=number, total_rounds=30,
                agent_id='INDEPENDENT_CHECK_API_01',
                available_slots=[dict(slot=slot, departure_minute=465 + slot,
                    departure_time=experiment.minute_to_clock(465 + slot)) for slot in range(1, 17)],
                cost_parameters=dict(fixed_travel_time_cost=experiment.C.FIXED_TRAVEL_TIME_COST,
                    queue_cost_per_minute=experiment.C.QUEUE_COST_PER_MINUTE,
                    early_cost_per_minute=experiment.C.EARLY_COST_PER_MINUTE,
                    late_cost_per_minute=experiment.C.LATE_COST_PER_MINUTE,
                    preferred_arrival_minute=experiment.C.PREFERRED_ARRIVAL_MINUTE,
                    preferred_arrival_time=experiment.minute_to_clock(experiment.C.PREFERRED_ARRIVAL_MINUTE),
                    free_flow_travel_minutes=experiment.C.FREE_FLOW_TRAVEL_MINUTES,
                    first_departure_minute=466, last_departure_minute=481,
                    capacity_window_minutes=experiment.C.CAPACITY_WINDOW_MINUTES),
                capacity_context=capacity_context, history=deepcopy(histories[version]),
                persona=deepcopy(report['persona']), limited_memory=memories[version])
            captured = {}
            def observed_post(actual_config, payload):
                captured['payload'] = deepcopy(payload)
                raw = transport(actual_config, payload)
                captured['response'] = deepcopy(raw)
                return raw
            choice = choose_agent_departure(config=current_config, choice_set=choice_set,
                                            http_post=observed_post)
            outcomes = settle_market(capacity, [choice.departure_slot] + scenario['opponent_slots'])
            own = outcomes[0]
            record = dict(round_number=number, prompt_version=version,
                request_payload=captured.get('payload'), raw_response=captured.get('response'),
                departure_slot=choice.departure_slot, reason=choice.reason,
                memory_summary=choice.memory_summary, latency_ms=choice.latency_ms,
                decision_source=choice.decision_source, fallback_used=choice.fallback_used,
                simulated_outcome=own)
            report['records'].append(record)
            # Match the production single-previous-round information boundary.
            histories[version] = dict(public_feedback=public_feedback(number, capacity, outcomes, capacity_config),
                own_previous_result=experiment.public_personal_result_from_record(
                    {'round_number': number, **own}))
            if choice.memory_summary is not None and config.limited_memory_enabled:
                memories[version] = choice.memory_summary
            if on_progress:
                on_progress(deepcopy(report))
    for version in VERSIONS:
        rows = [r for r in report['records'] if r['prompt_version'] == version]
        report['summary'][version] = dict(api_success_count=sum(not r['fallback_used'] for r in rows),
            average_latency_ms=round(mean(r['latency_ms'] for r in rows), 2),
            average_simulated_cost=round(mean(r['simulated_outcome']['total_cost'] for r in rows), 4),
            departure_slots=[r['departure_slot'] for r in rows],
            slot_changes=sum(a['departure_slot'] != b['departure_slot'] for a, b in zip(rows, rows[1:])))
    report['finished_utc'] = datetime.now(timezone.utc).isoformat()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--real-api', action='store_true', help='explicitly allow at most six paid calls')
    parser.add_argument('--output', type=Path, required=True, help='new report JSON path; existing files are refused')
    args = parser.parse_args()
    if not args.real_api:
        parser.error('No API calls made: --real-api is required')
    # Loads existing .env credentials, but never serializes config or headers.
    import settings
    config = config_from_session(settings.DYNAMIC_BOTTLENECK_ROUND_COMMON)
    if not config.api_key:
        parser.error('No API calls made: existing API key is not configured')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as output:
        def save(report):
            output.seek(0)
            output.write(json.dumps(report, ensure_ascii=False, indent=2))
            output.truncate()
            output.flush()
        report = run_comparison(config, on_progress=save)
        save(report)
    print(json.dumps(report['summary'], ensure_ascii=False))
    print(f'Saved six-call report: {args.output}')


if __name__ == '__main__':
    main()
