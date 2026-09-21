from otree.api import Bot, Submission, expect

from . import AccessGate


class PlayerBot(Bot):
    def play_round(self):
        expected_password = self.session.config.get('participant_password')
        expect(bool(expected_password), '==', True)
        yield Submission(
            AccessGate,
            {'access_password': expected_password},
        )
        expect(self.participant.vars.get('access_granted'), '==', True)
        expect(bool(self.participant.vars.get('access_granted_at_ts')), '==', True)
