"""Pure, versioned state transitions for a Session's shared decision window.

The caller persists the returned dictionary in ``Session.vars`` under a
round-specific key.  This module does not make a browser ACK a decision.
"""

PROTOCOL_VERSION = 1
DECISION_SECONDS = 60
RELEASE_LEAD_SECONDS = 5


def make_round_state(*, round_number, now_ts, expected_codes):
    codes = list(dict.fromkeys(expected_codes))
    return {
        'protocol': PROTOCOL_VERSION,
        'round_number': int(round_number),
        'phase': 'SHELL_LOADING',
        'prepare_started_ts': float(now_ts),
        'prepare_deadline_ts': float(now_ts) + (120 if int(round_number) == 1 else 60),
        'expected_codes': codes,
        'ack_ts_by_code': {},
        'shell_get_ingress_ts_by_code': {},
        'shell_rendered_ts_by_code': {},
        'excluded_codes': [],
        'release_ts': None,
        'deadline_ts': None,
        'technical_anomaly': '',
    }


def acknowledge_shell(state, participant_code, *, now_ts):
    if participant_code not in state['expected_codes']:
        return False
    if participant_code in state['excluded_codes']:
        return False
    if participant_code not in state['ack_ts_by_code'] and (
        float(now_ts) >= state['prepare_deadline_ts']
        or state['release_ts'] is not None
    ):
        return False
    if participant_code not in state['ack_ts_by_code']:
        state['ack_ts_by_code'][participant_code] = float(now_ts)
    return True


def mark_shell_rendered(state, participant_code, *, now_ts, ingress_ts=None):
    """Record when shell template processing starts, not response completion."""
    if participant_code not in state['expected_codes']:
        return False
    ingress_ts = float(now_ts if ingress_ts is None else ingress_ts)
    if ingress_ts >= state['prepare_deadline_ts']:
        return False
    if participant_code not in state['shell_rendered_ts_by_code']:
        state['shell_rendered_ts_by_code'][participant_code] = float(now_ts)
        state.setdefault('shell_get_ingress_ts_by_code', {})[participant_code] = ingress_ts
    return True


def release_if_ready(state, *, now_ts):
    if state['release_ts'] is not None:
        return True
    if state['technical_anomaly']:
        return False
    if not all(code in state['ack_ts_by_code'] for code in state['expected_codes']):
        return False
    release_ts = float(now_ts) + RELEASE_LEAD_SECONDS
    state['release_ts'] = release_ts
    state['deadline_ts'] = release_ts + DECISION_SECONDS
    state['phase'] = 'ARMED'
    return True


def expire_preparation(state, *, now_ts, pending_codes, drain_limit_seconds=None):
    """Release confirmed clients only if missing ones look genuinely absent.

    A processed shell without ACK, or an in-flight page request, is treated as
    a technical anomaly.  This errs on the side of excluding dubious data.
    """
    if state['release_ts'] is not None:
        return True
    if float(now_ts) < state['prepare_deadline_ts']:
        return False
    missing = set(state['expected_codes']) - set(state['ack_ts_by_code'])
    if missing & set(pending_codes):
        if (drain_limit_seconds is not None and
                float(now_ts) <= state['prepare_deadline_ts'] + drain_limit_seconds):
            state['phase'] = 'ACK_DRAINING'
            return False
        state['technical_anomaly'] = 'shell_ack_queue_overrun'
        state['phase'] = 'TECHNICAL_ANOMALY'
        return False
    if missing & set(state['shell_rendered_ts_by_code']):
        state['technical_anomaly'] = 'shell_not_acknowledged_or_page_queued'
        state['phase'] = 'TECHNICAL_ANOMALY'
        return False
    state['excluded_codes'] = sorted(missing)
    release_ts = float(now_ts) + RELEASE_LEAD_SECONDS
    state['release_ts'] = release_ts
    state['deadline_ts'] = release_ts + DECISION_SECONDS
    state['phase'] = 'ARMED'
    return True


def admission_kind(state, participant_code, ingress_ts):
    if participant_code in state['excluded_codes']:
        return 'excluded'
    if participant_code not in state['ack_ts_by_code']:
        return 'unacknowledged'
    if state['technical_anomaly']:
        return 'technical_anomaly'
    if state['release_ts'] is None or float(ingress_ts) < state['release_ts']:
        return 'early'
    if float(ingress_ts) >= state['deadline_ts']:
        return 'late'
    return 'manual'
