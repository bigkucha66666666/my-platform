"""oTree's ASGI application with pre-lock decision-arrival observation.

Use this entrypoint only after the room-release browser/load acceptance tests.
The existing ``otree prodserver`` command is not changed by this module.
"""
from otree.asgi import app as otree_app

from dynamic_bottleneck_round.decision_ingress import DecisionIngressMiddleware


app = DecisionIngressMiddleware(otree_app)
