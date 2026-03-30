from os import environ


SESSION_CONFIGS = [
    dict(
        name='route_choice_live',
        display_name="交通拥堵分析实验（正式场次）",
        app_sequence=['route_choice'],
        num_demo_participants=1,
    ),
    dict(
        name='route_choice_experiment',
        display_name="交通拥堵分析实验（演示场次）",
        app_sequence=['route_choice'],
        num_demo_participants=1,
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
        name='econ101',
        display_name='实验室房间（标签登录）',
        participant_label_file='_rooms/econ101.txt',
    ),
    dict(name='live_demo', display_name='演示房间（无需标签）'),
]

ADMIN_USERNAME = 'admin'
# for security, best to set admin password in an environment variable
ADMIN_PASSWORD = environ.get('OTREE_ADMIN_PASSWORD')

DEMO_PAGE_INTRO_HTML = """
请选择正式场次 route_choice_live，并通过房间标签进入。
"""


SECRET_KEY = '5946993890413'

INSTALLED_APPS = ['otree']
