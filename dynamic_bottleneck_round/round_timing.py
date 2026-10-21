"""Session-frozen page limits; existing Sessions retain their original limits."""

ROUND_TIMING_SESSION_VAR = 'dynamic_bottleneck_round_timing_v1'
ROUND_TIMING_VERSION = 'staged_v1'


def new_round_timing_rule():
    """Return a fresh, JSON-compatible snapshot for 3 warmups + 30 paid rounds."""
    pairs = [(60, 50)] * 3 + [(60, 40)] * 10 + [(40, 30)] * 10 + [(30, 30)] * 10
    return {'version': ROUND_TIMING_VERSION, 'rounds': [list(pair) for pair in pairs]}


def round_timing_for_session(session, round_number):
    stored = getattr(session, '_vars', None)
    if stored is None:
        stored = getattr(session, 'vars', {})
    if ROUND_TIMING_SESSION_VAR not in stored:
        return dict(decision_seconds=60, results_seconds=50,
                    timing_rule_version='legacy_60_50')
    rule = stored[ROUND_TIMING_SESSION_VAR]
    if (not isinstance(rule, dict) or rule.get('version') != ROUND_TIMING_VERSION
            or not isinstance(rule.get('rounds'), list) or len(rule['rounds']) != 33):
        raise ValueError('Invalid saved Session timing rule')
    index = int(round_number) - 1
    if not 0 <= index < len(rule['rounds']):
        raise ValueError('Round outside saved Session timing rule')
    pair = rule['rounds'][index]
    if (not isinstance(pair, (list, tuple)) or len(pair) != 2
            or any(type(seconds) is not int or seconds <= 0 for seconds in pair)):
        raise ValueError('Invalid saved round duration')
    return dict(decision_seconds=pair[0], results_seconds=pair[1],
                timing_rule_version=rule['version'])
