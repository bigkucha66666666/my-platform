"""oTree's ASGI application with pre-lock decision-arrival observation.

Use this entrypoint only after the room-release browser/load acceptance tests.
Start with ``python project_prodserver.py`` so standard oTree initialization
precedes this import. Direct uninitialized uvicorn loading is rejected.
"""
from dynamic_bottleneck_round import Player

if not hasattr(Player, '_setattr_fields'):
    raise RuntimeError(
        '统一放行 ASGI 入口需要标准 oTree 初始化；'
        '请使用 python project_prodserver.py [地址:端口] 启动。'
    )

from otree.asgi import app as otree_app

from dynamic_bottleneck_round.decision_ingress import DecisionIngressMiddleware


app = DecisionIngressMiddleware(otree_app)
