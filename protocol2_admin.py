"""Protect the protocol-2 barrier without replacing native authentication."""
from starlette.responses import Response
from starlette.routing import Route
from otree import settings
from otree.database import db
from otree.models import Participant, Session
from otree.views.admin import AdvanceSession, SessionMonitor

MESSAGE = '此 Session 使用协议 2，不能绕过同步/结算屏障强制推进。请先核对 Room 监控与同步诊断，按受控恢复流程处理。'


def protected(session):
    return ('dynamic_bottleneck_round' in session.config.get('app_sequence', [])
            and session._vars.get('dynamic_bottleneck_room_release_protocol_v1') == 2)


class ProtectedAdvanceSession(AdvanceSession):
    def post(self, request, code):
        session = db.get_or_404(Session, code=code)
        # oTree 6 accepts an individually selected participant; check that
        # target too, so another Session URL cannot bypass this protection.
        selected = self.get_post_data().get('selected_participant')
        participant = db.get_or_404(Participant, code=selected) if selected else None
        if protected(session) or (participant and protected(participant.session)):
            return Response(MESSAGE, status_code=409,
                            headers={'Cache-Control': 'no-store'})
        return super().post(request, code)


class ProtectedSessionMonitor(SessionMonitor):
    def get_template_name(self):
        return 'otree/Protocol2SessionMonitor.html'

    def vars_for_template(self):
        return dict(super().vars_for_template(), current_page_name='SessionMonitor',
                    protocol2_advance_blocked=protected(self.session),
                    protocol2_advance_message=MESSAGE)


def register_protocol2_admin_routes(application):
    replacements = {'AdvanceSession': ProtectedAdvanceSession,
                    'SessionMonitor': ProtectedSessionMonitor}
    for index, route in enumerate(application.router.routes):
        view = replacements.get(getattr(route, 'name', ''))
        if view is not None and getattr(route, 'endpoint', None) is not view:
            view._requires_login = settings.AUTH_LEVEL in {'STUDY', 'DEMO'}
            application.router.routes[index] = Route(route.path, view, name=route.name)
