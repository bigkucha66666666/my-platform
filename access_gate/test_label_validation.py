import unittest
from types import SimpleNamespace

from . import AccessGate
from .strict_room_labels import (
    STRICT_LOOKUP_MARKER,
    install_strict_room_label_lookup,
    strict_room_participant_lookup,
)


def make_player(*, actual_label, expected_label=None, password='gate'):
    participant = SimpleNamespace(
        label=actual_label,
        vars={},
    )
    if expected_label is not None:
        participant.vars['expected_room_label'] = expected_label
    return SimpleNamespace(
        participant=participant,
        session=SimpleNamespace(config={'participant_password': password}),
    )


class AccessGateLabelValidationTests(unittest.TestCase):
    def test_rejects_label_that_differs_from_expected_room_label(self):
        player = make_player(actual_label='P040', expected_label='P001')

        error = AccessGate.error_message(player, {'access_password': 'gate'})

        self.assertIn('P001', error)
        self.assertIn('标签', error)

    def test_accepts_matching_prebound_room_label(self):
        player = make_player(actual_label='P001', expected_label='P001')

        self.assertIsNone(
            AccessGate.error_message(player, {'access_password': 'gate'})
        )

    def test_preserves_legacy_flow_when_no_expected_label_is_bound(self):
        player = make_player(actual_label=None)

        self.assertIsNone(
            AccessGate.error_message(player, {'access_password': 'gate'})
        )

    def test_wrong_password_is_reported_before_label_mismatch(self):
        player = make_player(actual_label='P040', expected_label='P001')

        self.assertEqual(
            AccessGate.error_message(player, {'access_password': 'wrong'}),
            '口令错误，请重试。',
        )


class FakeParticipantQuery:
    def __init__(self, participants):
        self.participants = participants
        self.requested_label = None

    def filter_by(self, *, label):
        self.requested_label = label
        return self

    def first(self):
        return next(
            (
                participant
                for participant in self.participants
                if participant.label == self.requested_label
            ),
            None,
        )


class StrictRoomLookupTests(unittest.TestCase):
    def make_session(self, assignment='sequential'):
        participants = [
            SimpleNamespace(label='P001'),
            SimpleNamespace(label='P040'),
        ]
        return SimpleNamespace(
            config={'participant_label_assignment': assignment},
            pp_set=FakeParticipantQuery(participants),
        )

    def test_unknown_label_does_not_fall_back_to_an_unvisited_participant(self):
        fallback_calls = []

        result = strict_room_participant_lookup(
            self.make_session(),
            'P041',
            lambda session, label: fallback_calls.append((session, label)),
        )

        self.assertIsNone(result)
        self.assertEqual(fallback_calls, [])

    def test_exact_prebound_label_still_resolves(self):
        result = strict_room_participant_lookup(
            self.make_session(),
            'P040',
            lambda session, label: None,
        )

        self.assertEqual(result.label, 'P040')

    def test_legacy_sessions_keep_otree_fallback_behavior(self):
        sentinel = object()

        result = strict_room_participant_lookup(
            self.make_session(assignment=''),
            'P041',
            lambda session, label: sentinel,
        )

        self.assertIs(result, sentinel)

    def test_installer_replaces_otree_lookup_once(self):
        from otree.views import participant as participant_views

        install_strict_room_label_lookup()
        installed = participant_views.get_participant_by_label
        install_strict_room_label_lookup()

        self.assertIs(participant_views.get_participant_by_label, installed)
        self.assertTrue(getattr(installed, STRICT_LOOKUP_MARKER, False))


if __name__ == '__main__':
    unittest.main()
