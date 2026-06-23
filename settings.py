from os import environ
from pathlib import Path


def load_local_env():
    env_path = Path(__file__).resolve().parent / '.env'
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding='utf-8-sig').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        # Keep real environment variables highest priority.
        environ.setdefault(key, value)


load_local_env()

# oTree computes its default DEBUG before importing this project settings file.
# Re-declare DEBUG here so values loaded from .env can hide participant debug info.
DEBUG = environ.get('OTREE_PRODUCTION') in [None, '', '0']

PROD_PARTICIPANT_PASSWORD = environ.get('OTREE_PROD_PARTICIPANT_PASSWORD')
BROWSER_COMMAND = environ.get('BROWSER_COMMAND')

SESSION_CONFIGS = [
    dict(
        name='route_choice_prod',
        display_name="正式实验 · 新建 Session",
        app_sequence=['access_gate', 'route_choice', 'payment_info'],
        doc=(
            "用于真实被试与奖励发放。\n"
            "创建时先填写参与人数；如需固定小组，可设置手动分组参数。\n"
            "创建完成后，通过 prod_room 安全链接组织正式被试进入。"
        ),
        participant_password=PROD_PARTICIPANT_PASSWORD,
        cohort_size=5,
        grouping_enabled=0,
        manual_grouping_spec='',
        num_demo_participants=1,
    ),
    dict(
        name='route_choice_demo',
        display_name="演示测试 · 快速走查",
        app_sequence=['route_choice'],
        doc='用于页面走查和流程测试。创建时填写测试人数，随后可使用 demo_room 直接进入。',
        cohort_size=3,
        grouping_enabled=0,
        manual_grouping_spec='',
        num_demo_participants=3,
    ),
    dict(
        name='single_bottleneck_prod',
        display_name="正式实验 · 单瓶颈出发时间",
        app_sequence=['access_gate', 'single_bottleneck', 'payment_info'],
        doc=(
            "用于真实被试的单瓶颈出发时间实验。\n"
            "参与者在每轮选择出发时间，系统按瓶颈容量与早到/晚到成本计算收益。\n"
            "默认所有参与者进入同一个瓶颈组，并按实际人数自动校准单步粗收费。"
        ),
        participant_password=PROD_PARTICIPANT_PASSWORD,
        cohort_size=0,
        grouping_enabled=0,
        manual_grouping_spec='',
        reward_treatment_enabled=0,
        rewarded_slot_spec='',
        reward_bonus_points=8,
        departure_schedule_auto_enabled=1,
        departure_schedule_min_slots_each_side=10,
        coarse_toll_auto_enabled=1,
        coarse_toll_auto_min_toll=0,
        coarse_toll_auto_max_toll=40,
        coarse_toll_auto_toll_step=1,
        coarse_toll_auto_mode='auto',
        coarse_toll_auto_approx_refine_pool_size=8,
        coarse_toll_auto_approx_refine_iterations=160,
        coarse_toll_enabled=1,
        coarse_toll_slot_spec='10-12',
        coarse_toll_time_window_spec='07:53-07:55',
        coarse_toll_points=3,
        bottleneck_capacity_per_slot=2,
        payoff_source_var='single_bottleneck_total_payoff',
        final_payoff_label='单瓶颈出发时间实验',
        payoff_source_label='single_bottleneck 全 10 轮累计结果',
        payoff_rounds=10,
        num_demo_participants=1,
    ),
    dict(
        name='single_bottleneck_demo',
        display_name="演示测试 · 单瓶颈出发时间",
        app_sequence=['single_bottleneck'],
        doc=(
            "用于单瓶颈出发时间实验的流程演示。\n"
            "默认所有参与者进入同一个瓶颈组，并按实际人数自动校准单步粗收费。"
        ),
        cohort_size=0,
        grouping_enabled=0,
        manual_grouping_spec='',
        reward_treatment_enabled=0,
        rewarded_slot_spec='',
        reward_bonus_points=8,
        departure_schedule_auto_enabled=1,
        departure_schedule_min_slots_each_side=10,
        coarse_toll_auto_enabled=1,
        coarse_toll_auto_min_toll=0,
        coarse_toll_auto_max_toll=40,
        coarse_toll_auto_toll_step=1,
        coarse_toll_auto_mode='auto',
        coarse_toll_auto_approx_refine_pool_size=8,
        coarse_toll_auto_approx_refine_iterations=160,
        coarse_toll_enabled=1,
        coarse_toll_slot_spec='10-12',
        coarse_toll_time_window_spec='07:53-07:55',
        coarse_toll_points=3,
        bottleneck_capacity_per_slot=2,
        payoff_source_var='single_bottleneck_total_payoff',
        final_payoff_label='单瓶颈出发时间实验',
        payoff_source_label='single_bottleneck 全 10 轮累计结果',
        payoff_rounds=10,
        num_demo_participants=5,
    ),
]

# if you set a property in SESSION_CONFIG_DEFAULTS, it will be inherited by all configs
# in SESSION_CONFIGS, except those that explicitly override it.
# the session config can be accessed from methods in your apps as self.session.config,
# e.g. self.session.config['participation_fee']

SESSION_CONFIG_DEFAULTS = dict(
    real_world_currency_per_point=1.00,
    participation_fee=0.00,
    doc="",
    grouping_enabled=0,
    manual_grouping_spec="",
    payoff_source_var='route_choice_total_payoff',
    final_payoff_label='交通实验',
    payoff_source_label='route_choice 全 10 轮累计结果',
    payoff_rounds=10,
)

PARTICIPANT_FIELDS = [
    'is_dropout',
    'dropout_active',
    'dropout_reason',
    'has_recovered_after_disconnect',
    'finished',
]
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
        display_name='正式房间(标签登录,P001-P100)',
        participant_label_file='_rooms/econ101.txt',
        use_secure_urls=False,
    ),
    dict(name='demo_room', display_name='演示房间（无需标签）'),
]

ADMIN_USERNAME = 'admin'
# for security, best to set admin password in an environment variable
ADMIN_PASSWORD = environ.get('OTREE_ADMIN_PASSWORD')
AUTH_LEVEL = environ.get('OTREE_AUTH_LEVEL', 'STUDY')

DEMO_PAGE_TITLE = "实验控制台"

DEMO_PAGE_INTRO_HTML = """
<style>
  #admin-page-container {
    max-width: 1080px !important;
  }

  #admin-page-container .page-header {
    margin-bottom: 0;
    padding-bottom: 16px;
    border-bottom: 2px solid #e5e7eb;
  }

  #admin-page-container .page-header h1 {
    font-family: 'SF Mono', 'Cascadia Code', 'Consolas', 'Menlo', monospace;
    font-size: 15px;
    font-weight: 500;
    color: #374151;
    letter-spacing: 0;
  }

  #admin-page-container > div > .row {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(330px, 0.88fr);
    gap: 32px;
    align-items: start;
    margin-top: 20px;
  }

  #admin-page-container > div > .row > .col-md-9,
  #admin-page-container > div > .row > .col-md-3.card.bg-light {
    width: auto;
    max-width: none;
    padding-left: 0;
    padding-right: 0;
    float: none;
  }

  #admin-page-container .list-group {
    display: grid;
    gap: 2px;
  }

  #admin-page-container .list-group-item {
    border: none;
    border-left: 2px solid transparent;
    border-radius: 0;
    padding: 14px 16px;
    margin: 0;
    background: #ffffff;
    color: #374151;
    font-size: 15px;
    font-weight: 500;
    transition: border-color 120ms ease, background 120ms ease;
  }

  #admin-page-container .list-group-item:hover,
  #admin-page-container .list-group-item:focus {
    border-left-color: #0066cc;
    background: #fafbfc;
    text-decoration: none;
  }

  #admin-page-container .list-group-item:first-child {
    border-left-color: #0066cc;
    background: #f8faff;
  }

  #admin-page-container .list-group-item:nth-child(2) {
    border-left-color: #6b7280;
    background: #f9fafb;
  }

  #admin-page-container .session-config {
    display: grid;
    gap: 6px;
    padding: 16px 18px;
    border: none;
    border-left: 2px solid #e5e7eb;
    border-radius: 0;
    background: #ffffff;
    color: #1f2937;
    font-size: 16px;
    font-weight: 600;
    line-height: 1.35;
    transition: border-color 120ms ease, background 120ms ease;
  }

  #admin-page-container .session-config:hover,
  #admin-page-container .session-config:focus {
    border-left-color: #0066cc;
    background: #fafbfc;
    text-decoration: none;
  }

  #admin-page-container .session-config::before {
    content: none;
  }

  #admin-page-container .session-config::after {
    content: "进入创建页，设置人数与实验参数";
    color: #9ca3af;
    font-size: 12px;
    font-weight: 400;
    line-height: 1.45;
  }

  #admin-page-container .session-config:nth-child(1) {
    border-left-color: #0066cc;
    background: #f8faff;
  }

  #admin-page-container .session-config:nth-child(1)::after {
    color: #6b7280;
  }

  #admin-page-container .session-config:nth-child(2) {
    border-left-color: #6b7280;
    background: #f9fafb;
  }

  #admin-page-container .col-md-3.card.bg-light {
    border: 1px solid #e5e7eb;
    border-radius: 3px;
    background: #ffffff;
    box-shadow: none;
  }

  #admin-page-container .col-md-3.card.bg-light > .card-body {
    padding: 20px;
  }

  #admin-page-container .col-md-3.card.bg-light .card-title {
    font-family: 'SF Mono', 'Cascadia Code', 'Consolas', 'Menlo', monospace;
    font-size: 12px;
    font-weight: 500;
    color: #6b7280;
    text-transform: uppercase;
    letter-spacing: 0;
    margin-bottom: 14px;
  }

  .landing-shell {
    color: #1f2937;
  }

  .landing-header {
    margin-bottom: 28px;
  }

  .landing-title {
    margin: 0 0 8px;
    font-size: 20px;
    font-weight: 600;
    line-height: 1.3;
    color: #111827;
  }

  .landing-subtitle {
    margin: 0;
    color: #6b7280;
    font-size: 13px;
    line-height: 1.6;
    max-width: 44ch;
  }

  .landing-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
    gap: 14px;
    margin-bottom: 22px;
  }

  .landing-path {
    border: 1px solid #e5e7eb;
    border-left: 2px solid #d1d5db;
    border-radius: 2px;
    background: #ffffff;
    padding: 18px 18px 16px;
    transition: border-color 120ms ease;
  }

  .landing-path.is-formal {
    border-left-color: #0066cc;
  }

  .landing-path:hover {
    border-color: #9ca3af;
    border-left-color: #0066cc;
  }

  .landing-path-label {
    display: block;
    margin-bottom: 4px;
    font-family: 'SF Mono', 'Cascadia Code', 'Consolas', 'Menlo', monospace;
    font-size: 11px;
    font-weight: 500;
    color: #9ca3af;
    text-transform: uppercase;
  }

  .landing-path.is-formal .landing-path-label {
    color: #0066cc;
  }

  .landing-path-title {
    margin: 0 0 5px;
    font-size: 17px;
    font-weight: 600;
    line-height: 1.3;
  }

  .landing-path-copy {
    margin: 0 0 14px;
    color: #6b7280;
    font-size: 13px;
    line-height: 1.5;
  }

  .landing-flow {
    margin: 0;
    padding: 0;
    list-style: none;
  }

  .landing-flow li {
    display: flex;
    align-items: baseline;
    gap: 10px;
    padding: 8px 0;
    border-top: 1px solid #f3f4f6;
    color: #374151;
    font-size: 13px;
    line-height: 1.55;
  }

  .landing-flow li:first-child {
    border-top: none;
    padding-top: 0;
  }

  .landing-step {
    flex-shrink: 0;
    width: 18px;
    height: 18px;
    border-radius: 2px;
    background: #f3f4f6;
    color: #6b7280;
    font-family: 'SF Mono', 'Cascadia Code', 'Consolas', 'Menlo', monospace;
    font-size: 10px;
    font-weight: 500;
    line-height: 18px;
    text-align: center;
  }

  .landing-path.is-formal .landing-step {
    background: #eff6ff;
    color: #0066cc;
  }

  .landing-note {
    display: grid;
    gap: 5px;
    padding-top: 12px;
    border-top: 1px dotted #d1d5db;
    color: #9ca3af;
    font-size: 12px;
    line-height: 1.6;
  }

  .landing-note strong {
    color: #374151;
    font-weight: 600;
  }

  @media (max-width: 991px) {
    #admin-page-container {
      max-width: 970px !important;
    }

    #admin-page-container > div > .row {
      grid-template-columns: 1fr;
      gap: 22px;
    }

    #admin-page-container .session-config {
      font-size: 15px;
      padding: 14px 16px;
    }
  }
</style>

<section class="landing-shell">
  <div class="landing-header">
    <h1 class="landing-title">实验控制台</h1>
    <p class="landing-subtitle">
      选择实验类型新建 session 并设置参数，创建完成后通过对应房间组织参与者进入。
    </p>
  </div>

  <div class="landing-grid">
    <section class="landing-path is-formal">
      <span class="landing-path-label">Formal</span>
      <h2 class="landing-path-title">正式实验</h2>
      <p class="landing-path-copy">
        真实被试使用，数据纳入正式记录并用于报酬发放。
      </p>
      <ol class="landing-flow">
        <li><span class="landing-step">1</span><span>在左侧面板点击 <strong>Create new session</strong></span></li>
        <li><span class="landing-step">2</span><span>选择 <strong>正式实验 &middot; 新建 Session</strong>，填写人数，必要时设置手动分组</span></li>
        <li><span class="landing-step">3</span><span>通过 <strong>prod_room</strong> 安全链接组织被试进入</span></li>
      </ol>
    </section>

    <section class="landing-path">
      <span class="landing-path-label">Demo</span>
      <h2 class="landing-path-title">演示测试</h2>
      <p class="landing-path-copy">
        用于页面走查、功能验证和流程测试，数据不纳入正式记录。
      </p>
      <ol class="landing-flow">
        <li><span class="landing-step">1</span><span>在左侧面板点击 <strong>Create new session</strong></span></li>
        <li><span class="landing-step">2</span><span>选择 <strong>演示测试 &middot; 快速走查</strong>，填写测试人数</span></li>
        <li><span class="landing-step">3</span><span>使用 <strong>demo_room</strong> 进入，结束后到 Report 查看数据</span></li>
      </ol>
    </section>
  </div>

  <div class="landing-note">
    <span><strong>正式数据</strong> &mdash; 仅 formal run 的 session 作为正式实验记录与发放依据。</span>
    <span><strong>分组规则</strong> &mdash; 未启用手动分组时，cohort_size=0 表示不拆分，所有参与者进入同一组；cohort_size&gt;0 时按该人数自动分组。</span>
    <span><strong>报告查看</strong> &mdash; 每个 session 创建后可进入对应 Report 查看摘要与明细。</span>
  </div>
</section>
"""


SECRET_KEY = environ.get('OTREE_SECRET_KEY', 'dev-secret-key-change-me')

INSTALLED_APPS = ['otree']
