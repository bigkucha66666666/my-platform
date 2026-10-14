"""Session-specific administrator downloads, inside oTree's protected app."""
from datetime import date
from copy import deepcopy
from io import StringIO
import os
import sqlite3

from starlette.responses import Response
from starlette.routing import NoMatchFound, Route
from sqlalchemy.orm.attributes import set_committed_value

from otree import export, settings
from otree.models import Session
from otree.database import db
from otree.views.cbv import AdminView

from session_data_export import (
    HEADERS, INITIALIZED_ROUNDS_VAR, PROTOCOL_VAR, all_round_rows,
    encode_csv, read_sync_records, stored_vars, validate_session_code, warmup_round_count,
)


def write_raw_without_var_updates(buffer, session):
    """Undo only dirtiness caused by oTree's read-side .vars getters.

    Native wide export reads configured Participant/Session fields through
    .vars, whose getter calls Mutable.changed(). Suppress autoflush during
    that read and clean unchanged fields afterwards, preserving any preexisting
    dirty objects. Neither business values nor unrelated fields are reset.
    """
    objects = [session, *session.get_participants()]
    initially_dirty = set(db._db.dirty)
    snapshots = [(obj, deepcopy(stored_vars(obj))) for obj in objects]
    with db._db.no_autoflush:
        try:
            export.export_wide(buffer, session_code=session.code)
        finally:
            for obj, before in snapshots:
                if obj not in initially_dirty and stored_vars(obj) == before:
                    set_committed_value(obj, '_vars', obj._vars)


class SessionDataExport(AdminView):
    url_pattern = '/SessionDataExport/{code}/{export_kind}'

    def get(self, request, code, export_kind):
        try:
            validate_session_code(code)
        except ValueError as exc:
            return Response(str(exc), status_code=400)
        if export_kind not in {'all_rounds', 'raw'}:
            return Response('不支持该导出类型。', status_code=400)
        session = Session.objects_filter(code=code).first()
        if session is None:
            return Response('找不到指定的 Session。', status_code=404)
        if 'dynamic_bottleneck_round' not in session.config.get('app_sequence', []):
            return Response('该下载入口仅用于动态瓶颈 Session。', status_code=400)

        if export_kind == 'raw':
            buffer = StringIO(newline='')
            buffer.write('\ufeff')
            # Do not use the currently selected Report app/round or optional
            # query parameters: this code is mandatory and already validated.
            write_raw_without_var_updates(buffer, session)
            content = buffer.getvalue().encode('utf-8')
        else:
            import dynamic_bottleneck_round as experiment
            players = experiment.Player.objects_filter(session=session).all()
            values = stored_vars(session)
            records = {}
            faults = {}
            if values.get(PROTOCOL_VAR) == 2 and values.get(INITIALIZED_ROUNDS_VAR):
                try:
                    records = read_sync_records(
                        os.environ.get('DYNAMIC_ROOM_SYNC_DB', 'room_sync.sqlite3'), code,
                    )
                except (OSError, sqlite3.Error, ValueError):
                    return Response('无法读取已有同步诊断记录，请恢复诊断文件后重试；实验数据未修改。',
                                    status_code=503, headers={'Cache-Control': 'no-store'})
                # Snapshot the existing process-local interlock, without
                # get_journal()/persist_control_fault() or any history writes.
                faults = {number: experiment.sync_runtime.control_fault(code, number)
                          for number in values[INITIALIZED_ROUNDS_VAR]}
            rows = all_round_rows(session, players,
                                  warmup_rounds=warmup_round_count(session, players, default=experiment.C.WARMUP_ROUNDS),
                                  sync_records=records, runtime_faults=faults)
            content = encode_csv(HEADERS, ([row[header] for header in HEADERS] for row in rows))
        filename = f'session_{code}_{export_kind}_{date.today().isoformat()}.csv'
        return Response(content, media_type='text/csv; charset=utf-8', headers={
            'Content-Disposition': f'attachment; filename="{filename}"',
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
        })


def register_session_export_routes(application):
    """Install once per worker, retaining the platform's native login rules."""
    if any(getattr(route, 'name', '') == 'SessionDataExport' for route in application.routes):
        return
    SessionDataExport._requires_login = settings.AUTH_LEVEL in {'STUDY', 'DEMO'}
    application.router.routes.append(Route(
        SessionDataExport.url_pattern, SessionDataExport,
        methods=['GET'], name='SessionDataExport',
    ))


def session_export_links(code):
    from otree.asgi import app
    validate_session_code(code)
    try:
        return {
            'available': True,
            'all_rounds': str(app.url_path_for('SessionDataExport', code=code, export_kind='all_rounds')),
            'raw': str(app.url_path_for('SessionDataExport', code=code, export_kind='raw')),
        }
    except NoMatchFound:
        # Plain `otree prodserver` does not import project_asgi. Never silently
        # replace these links with a potentially unscoped upstream download.
        return {'available': False, 'all_rounds': '', 'raw': ''}
