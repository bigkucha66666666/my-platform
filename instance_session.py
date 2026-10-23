"""Validate instance conditions before native oTree creates/commits any rows."""
from functools import wraps
import inspect
import os
from types import SimpleNamespace

from instance_config import validate_session_condition


def validate_creation_request(config, *, room_name, condition):
    if room_name not in {None, f'prod_room_{condition.lower()}'}:
        raise ValueError(f'本实例只允许 prod_room_{condition.lower()} 房间。')
    if config.get('name') != 'dynamic_bottleneck_round_prod':
        raise ValueError('独立实例只允许动态瓶颈正式/预实验配置。')
    validate_session_condition(config, {}, condition=condition)


def install_session_guard():
    instance_id = os.environ.get('OTREE_INSTANCE_ID')
    if not instance_id:
        return
    import otree.session as native
    if getattr(native.create_session, '_instance_guard', False):
        return
    from dynamic_bottleneck_round import configure_formal_treatments
    original = native.create_session
    signature = inspect.signature(original)

    @wraps(original)
    def create_session(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        values = bound.arguments
        name = values['session_config_name']
        if name not in native.SESSION_CONFIGS_DICT:
            raise native.CreateSessionInvalidArgs('Session config not available in this instance')
        config = dict(native.SESSION_CONFIGS_DICT[name])
        config.update(values.get('modified_session_config_fields') or {})
        try:
            validate_creation_request(config, room_name=values['room_name'],
                                      condition=instance_id.upper())
            # Parse every pilot/formal group, not only the first group's condition.
            configure_formal_treatments(SimpleNamespace(config=config, vars={}))
        except ValueError as exc:
            raise native.CreateSessionInvalidArgs(str(exc)) from None
        return original(*args, **kwargs)

    create_session._instance_guard = True
    native.create_session = create_session
    # Normally views are imported later by otree.asgi. Cover an already loaded REST view too.
    import sys
    rest = sys.modules.get('otree.views.rest')
    if rest is not None:
        rest.create_session = create_session
