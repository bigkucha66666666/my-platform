"""Protect pre-bound Room labels from oTree's unknown-label fallback."""


STRICT_LOOKUP_MARKER = '_economics_experiment_strict_room_label_lookup'
LOOKUP_FUNCTION_NAMES = (
    'get_existing_or_new_participant',
    'get_participant_by_label',
)


def strict_room_participant_lookup(session, label, fallback_lookup):
    """Use exact labels for sequential sessions and legacy lookup otherwise."""

    assignment_mode = str(
        session.config.get('participant_label_assignment', '') or ''
    ).strip().lower()
    if assignment_mode != 'sequential':
        return fallback_lookup(session, label)
    if not label:
        return None
    return session.pp_set.filter_by(label=label).first()


def install_strict_room_label_lookup():
    """Install an idempotent project-level guard around oTree's Room lookup."""

    from otree.views import participant as participant_views

    found_lookup = False
    for function_name in LOOKUP_FUNCTION_NAMES:
        current_lookup = getattr(participant_views, function_name, None)
        if not callable(current_lookup):
            continue
        found_lookup = True
        if getattr(current_lookup, STRICT_LOOKUP_MARKER, False):
            continue

        def guarded_lookup(session, label, _fallback=current_lookup):
            return strict_room_participant_lookup(
                session,
                label,
                _fallback,
            )

        setattr(guarded_lookup, STRICT_LOOKUP_MARKER, True)
        setattr(participant_views, function_name, guarded_lookup)

    if not found_lookup:
        raise RuntimeError('Unsupported oTree participant-label lookup API.')
