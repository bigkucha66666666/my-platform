from os import environ
from pathlib import Path


def load_local_env():
    env_path = Path(__file__).resolve().parent / '.env'
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        # Keep real environment variables highest priority.
        environ.setdefault(key, value)


load_local_env()

PROD_PARTICIPANT_PASSWORD = environ.get('OTREE_PROD_PARTICIPANT_PASSWORD')

SESSION_CONFIGS = [
    dict(
        name='route_choice_prod',
        display_name="交通拥堵分析实验（正式运营）",
        app_sequence=['access_gate', 'route_choice', 'payment_info'],
        doc='正式场次：用于真实被试与奖励发放。',
        participant_password=PROD_PARTICIPANT_PASSWORD,
        num_demo_participants=1,
    ),
    dict(
        name='route_choice_demo',
        display_name="交通拥堵分析实验（演示测试）",
        app_sequence=['route_choice'],
        doc='演示场次：仅用于测试流程，不用于奖励发放。',
        num_demo_participants=3,
    ),
]

# if you set a property in SESSION_CONFIG_DEFAULTS, it will be inherited by all configs
# in SESSION_CONFIGS, except those that explicitly override it.
# the session config can be accessed from methods in your apps as self.session.config,
# e.g. self.session.config['participation_fee']

SESSION_CONFIG_DEFAULTS = dict(
    real_world_currency_per_point=1.00, participation_fee=0.00, doc=""
)

PARTICIPANT_FIELDS = []
SESSION_FIELDS = []

# ISO-639 code
# for example: de, fr, ja, ko, zh-hans
LANGUAGE_CODE = 'zh-hans'

# e.g. EUR, GBP, CNY, JPY
REAL_WORLD_CURRENCY_CODE = 'USD'
USE_POINTS = True

ROOMS = [
    dict(
        name='prod_room',
        display_name='正式房间（标签登录，P001-P050）',
        participant_label_file='_rooms/econ101.txt',
    ),
    dict(name='demo_room', display_name='演示房间（无需标签）'),
]

ADMIN_USERNAME = 'admin'
# for security, best to set admin password in an environment variable
ADMIN_PASSWORD = environ.get('OTREE_ADMIN_PASSWORD')
AUTH_LEVEL = environ.get('OTREE_AUTH_LEVEL', 'STUDY')

DEMO_PAGE_INTRO_HTML = """
正式运营：请选择 route_choice_prod，并使用 prod_room（标签登录）。
演示测试：请选择 route_choice_demo，并使用 demo_room（无需标签）。
"""


SECRET_KEY = environ.get('OTREE_SECRET_KEY', 'dev-secret-key-change-me')

INSTALLED_APPS = ['otree']
