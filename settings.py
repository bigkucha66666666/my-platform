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
            "默认按每组实际人数自动校准单步粗收费；关闭自动校准后可手动设置收费时段。"
        ),
        participant_password=PROD_PARTICIPANT_PASSWORD,
        cohort_size=5,
        grouping_enabled=0,
        manual_grouping_spec='',
        reward_treatment_enabled=0,
        rewarded_slot_spec='',
        reward_bonus_points=8,
        departure_schedule_auto_enabled=1,
        departure_schedule_min_slots_each_side=5,
        coarse_toll_auto_enabled=1,
        coarse_toll_auto_min_toll=0,
        coarse_toll_auto_max_toll=40,
        coarse_toll_auto_toll_step=1,
        coarse_toll_auto_mode='auto',
        coarse_toll_auto_approx_refine_pool_size=8,
        coarse_toll_auto_approx_refine_iterations=160,
        coarse_toll_enabled=1,
        coarse_toll_slot_spec='4-8',
        coarse_toll_time_window_spec='',
        coarse_toll_points=8,
        bottleneck_capacity_per_slot=1,
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
            "默认按每组实际人数自动校准单步粗收费；如需测试奖励，可设置 reward_treatment_enabled=1。"
        ),
        cohort_size=5,
        grouping_enabled=0,
        manual_grouping_spec='',
        reward_treatment_enabled=0,
        rewarded_slot_spec='',
        reward_bonus_points=8,
        departure_schedule_auto_enabled=1,
        departure_schedule_min_slots_each_side=5,
        coarse_toll_auto_enabled=1,
        coarse_toll_auto_min_toll=0,
        coarse_toll_auto_max_toll=40,
        coarse_toll_auto_toll_step=1,
        coarse_toll_auto_mode='auto',
        coarse_toll_auto_approx_refine_pool_size=8,
        coarse_toll_auto_approx_refine_iterations=160,
        coarse_toll_enabled=1,
        coarse_toll_slot_spec='4-8',
        coarse_toll_time_window_spec='',
        coarse_toll_points=8,
        bottleneck_capacity_per_slot=1,
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
        display_name='正式房间（标签登录，P001-P050）',
        participant_label_file='_rooms/econ101.txt',
        use_secure_urls=True,
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
    max-width: 1260px !important;
  }

  #admin-page-container .page-header {
    margin-bottom: 20px;
  }

  #admin-page-container > div > .row {
    display: grid;
    grid-template-columns: minmax(0, 1.08fr) minmax(360px, 0.92fr);
    gap: 26px;
    align-items: start;
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
    gap: 14px;
  }

  #admin-page-container .session-config {
    position: relative;
    display: grid;
    gap: 10px;
    padding: 22px 24px 22px;
    border: 1px solid #d9e4f5;
    border-radius: 22px;
    background:
      linear-gradient(180deg, rgba(249, 251, 255, 0.95) 0%, rgba(238, 245, 255, 0.98) 100%);
    color: #153357;
    font-size: 20px;
    font-weight: 700;
    line-height: 1.25;
    box-shadow: 0 14px 34px rgba(14, 34, 61, 0.07);
    transition: transform 180ms ease, border-color 180ms ease, box-shadow 180ms ease;
  }

  #admin-page-container .session-config:hover,
  #admin-page-container .session-config:focus {
    transform: translateY(-2px);
    border-color: #b9cfee;
    box-shadow: 0 18px 36px rgba(33, 70, 120, 0.12);
    text-decoration: none;
  }

  #admin-page-container .session-config::before {
    display: inline-flex;
    width: fit-content;
    padding: 6px 10px;
    border-radius: 999px;
    border: 1px solid #cddbf1;
    background: rgba(255, 255, 255, 0.84);
    color: #56749c;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  #admin-page-container .session-config:nth-child(1)::before {
    content: "Formal Run";
  }

  #admin-page-container .session-config:nth-child(2)::before {
    content: "Demo Run";
  }

  #admin-page-container .session-config::after {
    content: "进入创建页，设置人数与实验参数";
    color: #5b6f89;
    font-size: 13px;
    font-weight: 500;
    line-height: 1.55;
  }

  #admin-page-container .col-md-3.card.bg-light {
    border: 1px solid #d9e4f5;
    border-radius: 26px;
    background:
      radial-gradient(circle at top right, rgba(130, 182, 255, 0.16), transparent 34%),
      linear-gradient(180deg, #f9fbff 0%, #edf4ff 100%);
    box-shadow: 0 20px 44px rgba(16, 35, 61, 0.08);
  }

  #admin-page-container .col-md-3.card.bg-light > .card-body {
    padding: 22px;
  }

  .landing-shell {
    position: relative;
    overflow: hidden;
    margin: 0;
    padding: 0;
    border: none;
    border-radius: 0;
    background: transparent;
    color: #10233d;
  }

  .landing-header,
  .landing-grid,
  .landing-note {
    position: relative;
    z-index: 1;
  }

  .landing-kicker {
    display: inline-flex;
    align-items: center;
    padding: 6px 11px;
    border: 1px solid #c7d8f3;
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.78);
    color: #47668d;
    font-size: 11px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }

  .landing-header {
    display: grid;
    gap: 10px;
  }

  .landing-title {
    margin: 0;
    max-width: 14ch;
    font-size: clamp(26px, 2.1vw, 38px);
    line-height: 1.05;
    font-weight: 700;
    letter-spacing: -0.03em;
  }

  .landing-subtitle {
    margin: 0;
    max-width: 42ch;
    color: #506785;
    font-size: 14px;
    line-height: 1.7;
  }

  .landing-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
    gap: 14px;
    margin-top: 20px;
  }

  .landing-path {
    padding: 18px 18px 16px;
    border: 1px solid #d6e1f2;
    border-radius: 18px;
    background: rgba(255, 255, 255, 0.82);
    transition: transform 160ms ease, border-color 160ms ease, box-shadow 160ms ease;
  }

  .landing-path:hover {
    transform: translateY(-2px);
    border-color: #b7cbeb;
    box-shadow: 0 12px 26px rgba(47, 88, 148, 0.10);
  }

  .landing-path-label {
    display: inline-block;
    margin-bottom: 10px;
    color: #5677a4;
    font-size: 11px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }

  .landing-path-title {
    margin: 0 0 8px;
    font-size: 20px;
    line-height: 1.2;
    font-weight: 700;
  }

  .landing-path-copy {
    margin: 0 0 12px;
    color: #586d88;
    font-size: 13px;
    line-height: 1.55;
  }

  .landing-flow {
    margin: 0;
    padding: 0;
    list-style: none;
  }

  .landing-flow li {
    display: grid;
    grid-template-columns: 24px 1fr;
    gap: 10px;
    padding: 8px 0;
    border-top: 1px solid #e6edf8;
    color: #183152;
    font-size: 13px;
    line-height: 1.55;
  }

  .landing-flow li:first-child {
    border-top: none;
    padding-top: 0;
  }

  .landing-step {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 24px;
    height: 24px;
    border-radius: 50%;
    background: #e8f0ff;
    color: #315789;
    font-size: 11px;
    font-weight: 700;
  }

  .landing-note {
    display: grid;
    gap: 8px;
    margin-top: 16px;
    padding-top: 14px;
    border-top: 1px solid #d9e4f5;
    color: #5d718b;
    font-size: 12px;
    line-height: 1.6;
  }

  .landing-note strong {
    color: #10233d;
    font-weight: 600;
  }

  @media (max-width: 991px) {
    #admin-page-container {
      max-width: 970px !important;
    }

    #admin-page-container > div > .row {
      grid-template-columns: 1fr;
      gap: 18px;
    }

    #admin-page-container .session-config {
      font-size: 18px;
      padding: 18px 18px 20px;
    }
  }
</style>

<section class="landing-shell">
  <div class="landing-header">
    <div class="landing-kicker">实验管理员入口</div>
    <h1 class="landing-title">创建 session，再安排正式或演示流程</h1>
    <p class="landing-subtitle">
      左侧是创建入口，右侧是执行顺序。先新建 session 并设置人数，再按正式房间或演示房间组织进入。
    </p>
  </div>

  <div class="landing-grid">
    <section class="landing-path">
      <span class="landing-path-label">Formal Run</span>
      <h2 class="landing-path-title">正式实验</h2>
      <p class="landing-path-copy">
        真实被试使用，保留正式数据与支付结果。
      </p>
      <ol class="landing-flow">
        <li><span class="landing-step">1</span><span>在 Sessions 中点击 <strong>Create new session</strong>。</span></li>
        <li><span class="landing-step">2</span><span>选择 <strong>正式实验 · 新建 Session</strong>，填写人数，必要时设置手动分组。</span></li>
        <li><span class="landing-step">3</span><span>创建完成后，通过 <strong>prod_room</strong> 的安全链接组织正式被试进入。</span></li>
      </ol>
    </section>

    <section class="landing-path">
      <span class="landing-path-label">Demo Run</span>
      <h2 class="landing-path-title">演示测试</h2>
      <p class="landing-path-copy">
        用于页面走查、功能验证和排错。
      </p>
      <ol class="landing-flow">
        <li><span class="landing-step">1</span><span>在 Sessions 中点击 <strong>Create new session</strong>。</span></li>
        <li><span class="landing-step">2</span><span>选择 <strong>演示测试 · 快速走查</strong>，填写测试人数。</span></li>
        <li><span class="landing-step">3</span><span>创建后使用 <strong>demo_room</strong> 进入，测试完成后到 report 查看该 session 数据。</span></li>
      </ol>
    </section>
  </div>

  <div class="landing-note">
    <span><strong>正式数据：</strong>仅 `route_choice_prod` 的 session 作为正式实验记录与发放依据。</span>
    <span><strong>分组规则：</strong>未启用手动分组时，系统按 `cohort_size` 自动分组。</span>
    <span><strong>报告查看：</strong>每个 session 创建后，都可进入对应 report 查看本 session 的摘要与明细。</span>
  </div>
</section>
"""


SECRET_KEY = environ.get('OTREE_SECRET_KEY', 'dev-secret-key-change-me')

INSTALLED_APPS = ['otree']
