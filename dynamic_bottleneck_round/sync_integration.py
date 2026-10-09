"""oTree-only protocol 2 integration; control workers never import this module."""
import time
from . import sync_runtime as runtime
from .decision_ingress import (after_success, current_decision_shell_token,
    page_ingress_ts, request_ingress_ts, completed_shell_response_ts,
    completed_decision_response_ts, pending_participant_requests,
    pending_shell_acks, failed_participant_requests, completed_participant_requests_crossing)

IDENTITY_VAR = 'dynamic_room_sync_journal_identity_v2'
ROUNDS_VAR = 'dynamic_room_sync_initialized_rounds_v2'


def journal(session):
    stored = getattr(session, '_vars', None)
    if stored is None:
        stored = session.vars
    if stored.get(IDENTITY_VAR) != runtime.journal_identity():
        raise RuntimeError('Session sync journal identity changed')
    return runtime.get_journal()


def state(player, *, now_ts=None):
    import dynamic_bottleneck_round as e
    j = journal(player.session)
    number, session = player.round_number, player.session
    stored = getattr(session, '_vars', None)
    if stored is None:
        stored = session.vars
    if number not in stored.get(ROUNDS_VAR, []):
        from otree.lookup import _get_session_lookups
        decision_index = next(index for index, info in _get_session_lookups(session.code).items()
            if info.round_number == number and info.page_class.__name__ == 'Decision')
        players = player.subsession.get_players()
        conditions, capacities, levels = {}, {}, {}
        for p in players:
            context = e.public_capacity_context_for_group(p.group)
            capacity = e.capacity_record_for_group(p.group)
            code = p.participant.code
            conditions[code] = context['information_condition']
            capacities[code] = f'{capacity["actual_capacity"]:.2f}'
            levels[code] = e.capacity_level_label(capacity['capacity_level'])
        j.ensure_round(session.code, number,
            expected_codes=[p.participant.code for p in players if not e.participant_dropout_suspended(p)],
            now_ts=time.time() if now_ts is None else now_ts,
            decision_page_index=decision_index,
            drain_limit_seconds=stored[e.ROOM_RELEASE_DRAIN_VAR],
            information_condition=conditions, actual_capacity_display=capacities,
            capacity_level_label=levels, allowed_minutes=[int(item['minute']) for item in e.choice_preview(player)],
            boot_id=runtime.PROCESS_BOOT_ID)
        session.vars[ROUNDS_VAR] = [*stored.get(ROUNDS_VAR, []), number]
    reason = runtime.persist_control_fault(session.code, number)
    result = j.state(session.code, number)
    if reason and not result['settled']:
        result.update(technical_anomaly=reason, phase='TECHNICAL_ANOMALY')
    return result


def snapshot(player, *, kind='status', now_ts=None):
    now = time.time() if now_ts is None else float(now_ts)
    s = state(player, now_ts=now)
    j = journal(player.session)
    page, deadline = s['decision_page_index'], s['prepare_deadline_ts']
    if kind == 'render':
        token = current_decision_shell_token()
        # Validation-error native POST re-renders have no new GET nonce.
        if token:
            j.register_shell(player.session.code, player.round_number, player.participant.code,
                token, now_ts=now, ingress_ts=page_ingress_ts(), page_index=page)
    pending = set(runtime.pending_control_codes(player.session.code, player.round_number,
        kind='ack', before_ts=deadline))
    failed, crossed = [], []
    for code in s['expected_codes']:
        if (pending_participant_requests(code, before_ts=deadline, page_index=page)
                or pending_shell_acks(code, page_index=page, deadline_ts=deadline)):
            pending.add(code)
        if failed_participant_requests(code, page_index=page):
            failed.append(code)
        if completed_participant_requests_crossing(code, after_ts=s['prepare_started_ts'],
                deadline_ts=deadline, page_index=page):
            crossed.append(code)
    s = j.state(player.session.code, player.round_number, now_ts=now,
                pending_codes=pending, failed_codes=failed, crossed_codes=crossed)
    reason = runtime.persist_control_fault(player.session.code, player.round_number)
    if reason and not s['settled']:
        s.update(technical_anomaly=reason, phase='TECHNICAL_ANOMALY')
    # Public snapshot intentionally carries no private values into shell HTML.
    return dict((key, s[key]) for key in ('phase', 'prepare_deadline_ts', 'release_ts',
        'deadline_ts', 'technical_anomaly')) | {'server_now_ts': now,
        'acknowledged': player.participant.code in s['ack_ts_by_code']}


def control_token(player):
    s = state(player)
    token = current_decision_shell_token()
    if not token:
        token = journal(player.session).audit(player.session.code, player.round_number,
            player.participant.code)['shell_token']
    if not token:
        return ''
    return runtime.sign_control_token(player.session.code, player.round_number,
        player.participant.code, token, s['decision_page_index'])


def receipt(player):
    state(player)
    return journal(player.session).receipt(player.session.code, player.round_number,
        player.participant.code)


def native_audit(player):
    import dynamic_bottleneck_round as e
    ingress = request_ingress_ts()
    if ingress is not None:
        prefix = f'{e.ROOM_RELEASE_ROUND_PREFIX}{player.round_number}:'
        if prefix + 'post_ingress_ts' not in player.participant.vars:
            player.participant.vars[prefix + 'post_ingress_ts'] = ingress
            player.participant.vars[prefix + 'post_processing_ts'] = time.time()


def import_manual(player):
    import dynamic_bottleneck_round as e
    if player.group.results_ready or e.player_has_departure_choice(player):
        return
    r = receipt(player)
    if r and r['kind'] == 'manual':
        if e.set_player_departure_choice(player, r['departure_minute'], e.DECISION_SOURCE_MANUAL):
            e.record_manual_decision(player)
        player.timeout_happened = False


def ready_for_results(group):
    import dynamic_bottleneck_round as e
    players = group.get_players()
    snapshot(players[0])
    s = state(players[0])
    j = journal(group.session)
    if s['technical_anomaly'] or s['release_ts'] is None or time.time() < s['release_ts']:
        return False
    if any(failed_participant_requests(code, after_ts=s['release_ts'],
            before_ts=s['deadline_ts'], page_index=s['decision_page_index'])
            for code in s['expected_codes']):
        j.mark_anomaly(group.session.code, group.round_number, 'decision_page_http_failure')
        return False
    for other in players[0].subsession.get_groups():
        if not other.round_started:
            other.round_started = True
            other.round_started_at_ts = s['release_ts']
            other.decision_deadline_ts = s['deadline_ts']
            e.fill_suspended_choices(other)
    pending = runtime.pending_control_codes(group.session.code, group.round_number,
        kind='decision')
    if pending:
        if time.time() >= s['deadline_ts'] + s['drain_limit_seconds']:
            j.mark_anomaly(group.session.code, group.round_number, 'decision_control_queue_overrun')
        return False
    for p in players:
        import_manual(p)
    # Recheck after reading receipts: complete-body arrivals are registered
    # independently while the native business loop imports the first receipts.
    if runtime.persist_control_fault(group.session.code, group.round_number):
        return False
    if runtime.pending_control_codes(group.session.code, group.round_number,
            kind='decision'):
        return False
    if e.all_players_have_choice(group):
        return True
    if time.time() < s['deadline_ts'] + s['drain_limit_seconds']:
        return False
    for p in players:
        if e.player_has_departure_choice(p):
            continue
        r = receipt(p)
        if runtime.persist_control_fault(group.session.code, group.round_number):
            return False
        if r and r['kind'] == 'manual':
            # A control write may commit between the initial import and its
            # pending recheck. Canonical manual receipt still wins at drain.
            import_manual(p)
        elif r and r['kind'] == 'late':
            strategy = e.set_automatic_departure_choice(p, e.DECISION_SOURCE_TIMEOUT_AUTO)
            e.mark_timeout(p, strategy)
    if runtime.persist_control_fault(group.session.code, group.round_number):
        return False
    e.fill_missing_choices(group)
    return True


def schedule_settled(group):
    session_code, number = group.session.code, group.round_number
    # A control worker may fail while native result calculation is in progress.
    # Raising here lets the original oTree transaction roll its changes back.
    if runtime.persist_control_fault(session_code, number):
        raise RuntimeError('control failure during result settlement')
    if all(g.results_ready for g in group.subsession.get_groups()):
        j = journal(group.session)
        def settled():
            if not runtime.close_control_round(session_code, number):
                runtime.latch_control_fault(dict(session_code=session_code, round_number=number),
                    'control_pending_at_settlement')
                runtime.persist_control_fault(session_code, number)
                return
            try:
                j.mark_settled(session_code, number)
            except Exception:
                runtime.fail_control_settlement(session_code, number)
                raise
        after_success(settled,
                      key=('sync-settled', session_code, number))


def export_metadata(player):
    import dynamic_bottleneck_round as e
    j = journal(player.session)
    values = dict.fromkeys(e.ROOM_RELEASE_EXPORT_FIELDS, '')
    values['room_release_protocol'] = 2
    session_vars = getattr(player.session, '_vars', None)
    if session_vars is None:
        session_vars = player.session.vars
    if player.round_number not in session_vars.get(ROUNDS_VAR, []):
        return values
    # Exports inspect only existing clocks. A marked-but-missing round fails
    # closed rather than creating replacement history.
    reason = runtime.persist_control_fault(player.session.code, player.round_number)
    s = j.state(player.session.code, player.round_number)
    if reason and not s['settled']:
        s.update(technical_anomaly=reason, phase='TECHNICAL_ANOMALY')
    audit = j.audit(player.session.code, player.round_number,
        player.participant.code)
    values.update(room_release_protocol=2, room_release_phase=s['phase'],
        room_release_technical_anomaly=s['technical_anomaly'],
        room_release_exclude_behavior=bool(s['technical_anomaly']))
    for field in ('prepare_started_ts', 'prepare_deadline_ts', 'release_ts', 'deadline_ts'):
        values['room_release_' + field] = s[field] if s[field] is not None else ''
    for target, source in [('shell_get_ingress_ts', 'shell_get_ingress_ts'),
            ('shell_render_started_ts', 'shell_rendered_ts'), ('ack_ts', 'ack_ts'),
            ('operative_ts', 'operative_ts')]:
        values['room_release_' + target] = audit[source] if audit[source] is not None else ''
    values['room_release_shell_response_completed_ts'] = completed_shell_response_ts(
        player.participant.code, page_index=audit['page_index'], shell_token=audit['shell_token']) or ''
    r = audit['receipt'] or {}
    values['room_release_receipt_ingress_ts'] = r.get('ingress_ts', '')
    values['room_release_receipt_kind'] = r.get('kind', '')
    stored = getattr(player.participant, '_vars', None)
    if stored is None:
        stored = player.participant.vars
    prefix = f'{e.ROOM_RELEASE_ROUND_PREFIX}{player.round_number}:'
    for field in ('post_ingress_ts', 'post_processing_ts'):
        values['room_release_' + field] = stored.get(prefix + field, '')
    ingress = values['room_release_post_ingress_ts']
    if ingress != '':
        values['room_release_post_response_completed_ts'] = completed_decision_response_ts(
            player.participant.code, page_index=audit['page_index'], method='POST', ingress_ts=ingress) or ''
    return values
