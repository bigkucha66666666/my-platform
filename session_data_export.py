"""Read-only, all-round Session exports; no oTree initialization or Agent calls."""
import csv
from decimal import Decimal
from io import StringIO
import json
from pathlib import Path
import re
import sqlite3
from contextlib import closing


LLM_STORE = 'dynamic_bottleneck_round_agent_decisions_by_round_v1'
RL_STORE = 'dynamic_bottleneck_round_independent_rl_decisions_by_round_v1'
PROTOCOL_VAR = 'dynamic_bottleneck_room_release_protocol_v1'
INITIALIZED_ROUNDS_VAR = 'dynamic_room_sync_initialized_rounds_v2'
LEGACY_ROUND_PREFIX = 'dynamic_bottleneck_room_release_round_v1:'
TREATMENTS_VAR = 'dynamic_bottleneck_round_group_treatments_v1'

HEADERS = (
    'session_code', 'group_id', 'round_number', 'round_phase',
    'phase_round_number', 'formal_round_number', 'results_ready',
    'actor_type', 'participant_code', 'participant_label', 'agent_id',
    'treatment_condition', 'actor_composition', 'information_condition',
    'flow_preview_enabled', 'pilot_mode_enabled',
    'planned_group_human_count', 'planned_group_api_count',
    'planned_group_rl_count', 'planned_group_total_count',
    'dynamic_capacity', 'actual_capacity', 'capacity_level',
    'capacity_distribution', 'capacity_mu', 'capacity_sigma',
    'capacity_min', 'capacity_max', 'capacity_sequence_id',
    'capacity_sequence_seed', 'capacity_revealed_before_decision',
    'departure_slot', 'departure_minute', 'departure_time',
    'queue_delay_minutes', 'travel_time_minutes', 'arrival_minute',
    'arrival_time', 'early_minutes', 'late_minutes', 'slot_load',
    'total_cost', 'payoff', 'coarse_toll_charge', 'reward_bonus',
    'decision_source', 'timeout_happened', 'dropout_event',
    'recovered_this_round', 'recovery_reason',
    'room_release_protocol', 'room_release_phase',
    'room_release_technical_anomaly', 'room_release_exclude_behavior',
    'room_release_export_audit_status', 'room_release_prepare_started_ts',
    'room_release_prepare_deadline_ts', 'room_release_release_ts',
    'room_release_deadline_ts', 'agent_type', 'agent_fallback_used',
    'agent_latency_ms', 'agent_reason', 'agent_context_json',
    'agent_memory_input', 'agent_memory_output', 'agent_persona_id',
    'agent_persona_label', 'agent_policy_version', 'agent_prompt_version', 'rl_rounds_observed',
)

CHOICE_FIELDS = {
    'departure_slot': 'departure_slot', 'departure_minute': 'departure_minute',
    'departure_time': 'departure_time_label',
}
RESULT_FIELDS = {
    'queue_delay_minutes': 'queue_delay_minutes',
    'travel_time_minutes': 'travel_time_minutes',
    'arrival_minute': 'arrival_minute', 'arrival_time': 'arrival_time_label',
    'early_minutes': 'early_minutes', 'late_minutes': 'late_minutes',
    'slot_load': 'slot_load', 'total_cost': 'total_cost',
    'payoff': 'payoff_unrounded', 'coarse_toll_charge': 'coarse_toll_charge',
    'reward_bonus': 'reward_bonus',
}
CAPACITY_FIELDS = (
    'dynamic_capacity', 'capacity_level', 'capacity_distribution',
    'capacity_min', 'capacity_max', 'capacity_sequence_id',
    'capacity_sequence_seed', 'capacity_revealed_before_decision',
)
AGENT_FIELDS = {
    'agent_type': 'agent_type', 'agent_fallback_used': 'fallback_used',
    'agent_latency_ms': 'latency_ms', 'agent_reason': 'reason',
    'agent_context_json': 'context_json', 'agent_memory_input': 'memory_input',
    'agent_memory_output': 'memory_output', 'agent_persona_id': 'persona_id',
    'agent_persona_label': 'persona_label', 'agent_policy_version': 'policy_version',
    'agent_prompt_version': 'prompt_version',
    'rl_rounds_observed': 'rounds_observed',
}


def validate_session_code(code):
    if not isinstance(code, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', code):
        raise ValueError('必须指定有效的 Session 编号；不允许导出全部 Session。')
    return code


def stored_vars(obj):
    """oTree's public .vars getter marks its ORM field dirty even on a read."""
    values = getattr(obj, '_vars', None)
    return values if isinstance(values, dict) else {}


def warmup_round_count(session, players, *, default):
    """Prefer saved phase metadata to today's constants for historical data.

    This project records the formal-round count as payoff_rounds; when the
    Session has all precreated rows, its small leading practice block is the
    difference. If historical metadata is absent/inconsistent, keep the current
    scene default; raw round_number always remains available for verification.
    """
    explicit = session.config.get('warmup_rounds')
    if explicit is not None:
        if isinstance(explicit, bool) or not str(explicit).isdigit():
            raise ValueError('保存的热身轮次配置无效。')
        return int(explicit)
    formal = session.config.get('payoff_rounds')
    if not isinstance(formal, bool) and str(formal).isdigit() and int(formal) > 0:
        total = max((int(player.round_number) for player in players), default=0)
        if 0 <= total - int(formal) <= 10:
            return total - int(formal)
    return default


def _value(obj, name):
    if isinstance(obj, dict):
        value = obj.get(name)
    elif hasattr(obj, 'field_maybe_none'):
        try:
            value = obj.field_maybe_none(name)
        except AttributeError:
            value = None
    else:
        value = getattr(obj, name, None)
    return '' if value is None else value


def _flag(value):
    return value is True or str(value).lower() in {'1', 'true', 'yes', 'on'}


def read_sync_records(path, session_code):
    """Read existing durable history, without initializing or advancing clocks."""
    validate_session_code(session_code)
    path = Path(path).absolute()
    if not path.exists():
        return {}
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)) as connection:
        connection.execute('PRAGMA query_only=ON')
        rows = connection.execute(
            'SELECT round_number, record FROM sync_rounds WHERE session_code=?',
            (session_code,),
        ).fetchall()
    records = {}
    for number, encoded in rows:
        record = json.loads(encoded)
        if not isinstance(record, dict) or not isinstance(record.get('state'), dict):
            raise ValueError('同步诊断记录格式错误，无法可靠导出。')
        records[int(number)] = record
    return records


def _sync_values(session, number, sync_records, runtime_faults):
    values = stored_vars(session)
    protocol = values.get(PROTOCOL_VAR)
    if protocol not in (1, 2):
        return {'room_release_export_audit_status': 'not_enabled'}
    if protocol == 2:
        initialized = number in values.get(INITIALIZED_ROUNDS_VAR, [])
        record = sync_records.get(number, {})
        state = record.get('state', {})
    else:
        state = values.get(LEGACY_ROUND_PREFIX + str(number), {})
        initialized = bool(state)
    status = 'available' if state else ('unavailable' if initialized else 'not_initialized')
    anomaly = state.get('technical_anomaly', '')
    if protocol == 2 and initialized and not state.get('settled'):
        anomaly = anomaly or runtime_faults.get(number, '')
    result = {
        'room_release_protocol': protocol,
        'room_release_export_audit_status': status,
        'room_release_phase': state.get('phase', ''),
        'room_release_technical_anomaly': anomaly,
        'room_release_exclude_behavior': bool(anomaly) or status == 'unavailable',
    }
    for field in ('prepare_started_ts', 'prepare_deadline_ts', 'release_ts', 'deadline_ts'):
        result['room_release_' + field] = _value(state, field)
    return result


def _group_values(session, player, warmup_rounds, sync_records, runtime_faults):
    number = int(player.round_number)
    warmup = number <= warmup_rounds
    group_id = player.group.id_in_subsession
    treatment = stored_vars(session).get(TREATMENTS_VAR, {}).get(f'G{group_id:02d}', {})
    composition = _value(player, 'actor_composition') or treatment.get('actor_composition', '')
    information = _value(player, 'information_condition') or treatment.get('information_condition', '')
    row = dict.fromkeys(HEADERS, '')
    row.update(
        session_code=session.code, group_id=group_id, round_number=number,
        round_phase='warmup' if warmup else 'formal',
        phase_round_number=number if warmup else number - warmup_rounds,
        formal_round_number='' if warmup else number - warmup_rounds,
        results_ready=bool(player.group.results_ready),
        actor_composition=composition, information_condition=information,
        treatment_condition=f'{composition}-{information}' if composition and information else '',
        flow_preview_enabled=_flag(session.config.get('flow_preview_enabled', False)),
        pilot_mode_enabled=_flag(session.config.get('pilot_mode_enabled', False)),
    )
    for field in CAPACITY_FIELDS:
        row[field] = _value(player, field)
    row['actual_capacity'] = row['dynamic_capacity']
    for field in ('capacity_mu', 'capacity_sigma'):
        row[field] = session.config.get(field, '')
    for key in ('human', 'api', 'rl'):
        row[f'planned_group_{key}_count'] = treatment.get(key, '')
    if all(row[f'planned_group_{key}_count'] != '' for key in ('human', 'api', 'rl')):
        row['planned_group_total_count'] = sum(int(treatment[key]) for key in ('human', 'api', 'rl'))
    row.update(_sync_values(session, number, sync_records, runtime_faults))
    return row


def _actor_values(row, source, chosen):
    for target, field in CHOICE_FIELDS.items():
        row[target] = _value(source, field) if chosen else ''
    for target, field in RESULT_FIELDS.items():
        row[target] = _value(source, field) if chosen and row['results_ready'] else ''
    if chosen and row['results_ready'] and row['payoff'] == '':
        # Older virtual decisions stored only payoff; never substitute the
        # reference human's payoff, and never turn missing history into zero.
        row['payoff'] = _value(source, 'payoff')
    row['decision_source'] = _value(source, 'decision_source')
    return row


def all_round_rows(session, players, *, warmup_rounds, sync_records=None, runtime_faults=None):
    """Human model rows plus only already-saved virtual records, all phases."""
    validate_session_code(session.code)
    if isinstance(warmup_rounds, bool) or not isinstance(warmup_rounds, int) or warmup_rounds < 0:
        raise ValueError('热身轮次数必须是非负整数。')
    players = sorted(players, key=lambda p: (p.round_number, p.group.id_in_subsession, p.participant.code))
    if any(player.session.code != session.code for player in players):
        raise ValueError('目标 Session 的导出不能混入其他 Session。')
    groups = {}
    rows = []
    for player in players:
        key = (player.round_number, player.group.id_in_subsession)
        groups.setdefault(key, []).append(player)
        row = _group_values(session, player, warmup_rounds, sync_records or {}, runtime_faults or {})
        row.update(actor_type='human', participant_code=player.participant.code,
                   participant_label=player.participant.label or '')
        for field in ('timeout_happened', 'dropout_event', 'recovered_this_round', 'recovery_reason'):
            row[field] = _value(player, field)
        chosen = bool(_value(player, 'decision_source')) or _value(player, 'departure_slot') != ''
        rows.append(_actor_values(row, player, chosen))
    for (number, group_id), group_players in sorted(groups.items()):
        # The experiment stores virtual records on the first id_in_group human,
        # not necessarily the first alphabetically-sorted participant code.
        representative = min(group_players, key=lambda p: _value(p, 'id_in_group') or 1)
        records = {}
        values = stored_vars(representative.participant)
        for key in (LLM_STORE, RL_STORE):
            by_round = values.get(key, {})
            saved = by_round.get(str(number), []) if isinstance(by_round, dict) else []
            for record in saved if isinstance(saved, list) else []:
                if not isinstance(record, dict):
                    continue
                actor_type = record.get('actor_type', 'deepseek_api_agent')
                agent_id = record.get('agent_id')
                if not agent_id or actor_type not in {'deepseek_api_agent', 'api_agent', 'rl_agent'}:
                    continue
                if str(record.get('group_id', group_id)) != str(group_id):
                    continue
                if record.get('session_code', session.code) != session.code:
                    continue
                records[(actor_type, str(agent_id))] = record
        for (actor_type, agent_id), record in sorted(records.items()):
            row = _group_values(session, representative, warmup_rounds, sync_records or {}, runtime_faults or {})
            row.update(actor_type=actor_type, agent_id=agent_id)
            for field in CAPACITY_FIELDS:
                if field in record:
                    row[field] = _value(record, field)
            row['actual_capacity'] = row['dynamic_capacity']
            for target, field in AGENT_FIELDS.items():
                row[target] = _value(record, field)
            rows.append(_actor_values(row, record, _value(record, 'departure_minute') != ''))
    yield from sorted(rows, key=lambda row: (row['round_number'], row['group_id'],
                     row['actor_type'], row['participant_code'] or row['agent_id']))


def _csv_value(value):
    if value is None:
        return ''
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, Decimal):
        # oTree Currency overrides __str__ with a localized points/unit label.
        # Reconstruct the base Decimal so the analysis column stays numeric.
        return str(Decimal(value))
    if isinstance(value, str) and value.lstrip(' \t\r\n').startswith(('=', '+', '-', '@')):
        return "'" + value
    return value


def encode_csv(headers, rows):
    """Excel-friendly UTF-8; quote CSV syntax and neutralize text formulas."""
    buffer = StringIO(newline='')
    buffer.write('\ufeff')
    writer = csv.writer(buffer)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([_csv_value(value) for value in row])
    return buffer.getvalue().encode('utf-8')
