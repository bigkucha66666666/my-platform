"""Protect pre-bound Room labels from oTree's unknown-label fallback."""


STRICT_LOOKUP_MARKER = '_economics_experiment_strict_room_label_lookup'


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

    current_lookup = participant_views.get_participant_by_label
    if getattr(current_lookup, STRICT_LOOKUP_MARKER, False):
        return

    def guarded_lookup(session, label):
        return strict_room_participant_lookup(
            session,
            label,
            current_lookup,
        )

    setattr(guarded_lookup, STRICT_LOOKUP_MARKER, True)
    participant_views.get_participant_by_label = guarded_lookup
